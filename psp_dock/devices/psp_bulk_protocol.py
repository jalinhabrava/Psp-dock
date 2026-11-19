"""PSP Dock experimental *single-role* HID + bulk file transport.

Protocol/model only: NO PSP USB driver has been installed, and this
module never contacts a real device or mounts the PSP Memory Stick.
It is intended for a future composite HID + vendor-bulk interface:
the PSP firmware remains the SOLE owner of FAT throughout the session,
and the PC requests files over bulk rather than switching to USBSTOR.

The deterministic in-process transport below exercises frame integrity,
session binding, replay prevention and verified PC-side file streaming.
Actual PSPDEV endpoint drivers, WinUSB transport and live hardware
attestation still have to be implemented/tested separately.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import struct
import uuid
import zlib


MAGIC=b"PDB2"
VERSION=1
HEADER=struct.Struct("<4sBBHI16sQI")
CRC=struct.Struct("<I")
MAX_PAYLOAD=60*1024
MAX_FRAME=HEADER.size+CRC.size+MAX_PAYLOAD
MAX_MANIFEST_PATH=256


class BulkProtocolError(ValueError):
    pass


class Op(IntEnum):
    HELLO=1
    HELLO_ACK=2
    STAT=3
    META=4
    READ=5
    DATA=6
    EOF=7
    LIST=8
    LIST_DATA=9
    ERROR=255


def validated_path(value:str)->Path:
    """Only game ISOs/homebrew and save files, never PSP firmware/flash."""
    if (not isinstance(value,str) or not value or
            len(value.encode('utf-8'))>MAX_MANIFEST_PATH or
            '\0' in value or '\\' in value or ':' in value or
            value.startswith('/') or '//' in value):
        raise BulkProtocolError('Ruta PSP no permitida.')
    p=PurePosixPath(value)
    if (not 2<=len(p.parts)<=8 or
            any(x in ('..','.','') or x.startswith('.') for x in p.parts)):
        raise BulkProtocolError('Nombre relativo inseguro.')
    if p.parts[0]=='ISO' and len(p.parts)==2 and p.suffix.lower() in ('.iso','.cso'):
        return Path(*p.parts)
    if (p.parts[:2]==('PSP','GAME') and len(p.parts)>=4 and
            p.parts[2] and len(p.parts[2])<=80 and
            p.suffix.lower() in ('.pbp','.png','.at3','.dat','.txt','.prx')):
        return Path(*p.parts)
    if (p.parts[:2]==('PSP','SAVEDATA') and len(p.parts)>=4 and
            p.parts[2] and len(p.parts[2])<=80):
        # Retain original folder paths and byte-perfect savedata but never
        # allow arbitrary dynamic writes or access to PSP system partitions.
        return Path(*p.parts)
    raise BulkProtocolError('No es un recurso de juego o SAVEDATA autorizado.')


@dataclass(frozen=True)
class Frame:
    op: Op
    session: bytes
    seq: int
    payload: bytes = b''
    offset: int = 0

    def encode(self)->bytes:
        if (not isinstance(self.op,Op) or type(self.seq) is not int or
                not 1<=self.seq<=0xffffffff or
                not isinstance(self.session,bytes) or
                len(self.session)!=16 or not any(self.session) or
                type(self.offset) is not int or not 0<=self.offset<=0xffffffffffffffff
                or not isinstance(self.payload,bytes) or
                len(self.payload)>MAX_PAYLOAD):
            raise BulkProtocolError('PDB2: trama no válida.')
        header=HEADER.pack(MAGIC,VERSION,int(self.op),0,self.seq,
                           self.session,self.offset,len(self.payload))
        checksum=zlib.crc32(header+self.payload)&0xffffffff
        return header+CRC.pack(checksum)+self.payload

    @classmethod
    def decode(cls,blob:bytes)->'Frame':
        if not isinstance(blob,bytes) or len(blob)<HEADER.size+CRC.size:
            raise BulkProtocolError('PDB2: trama incompleta.')
        magic,version,op,flags,seq,session,offset,length=HEADER.unpack_from(blob)
        if (magic!=MAGIC or version!=VERSION or flags!=0 or
                not 0<seq<=0xffffffff or not any(session) or
                length>MAX_PAYLOAD or len(blob)!=HEADER.size+CRC.size+length):
            raise BulkProtocolError('PDB2: encabezado rechazado.')
        try:
            operation=Op(op)
        except ValueError as exc:
            raise BulkProtocolError('PDB2: operación desconocida.') from exc
        expected=CRC.unpack_from(blob,HEADER.size)[0]
        body=blob[HEADER.size+CRC.size:]
        if expected!=(zlib.crc32(blob[:HEADER.size]+body)&0xffffffff):
            raise BulkProtocolError('PDB2: CRC incorrecto.')
        return cls(operation,session,seq,body,offset)


class InProcessPSPFileServer:
    """Conservative simulation of the future PSP-resident read-only server.

    It is NOT an implementation of sceUsb or WinUSB and must not be used
    against a mounted real PSP FAT volume.
    """

    def __init__(self,root:Path, *,volume_serial:str):
        self.root=Path(root).resolve()
        if (not self.root.is_dir() or self.root.is_symlink() or
                not re.fullmatch('[0-9]{1,20}',volume_serial)):
            raise BulkProtocolError('Solo se admite una carpeta de prueba.')
        self.serial=volume_serial
        self.session:bytes|None=None
        self.next_seq=1

    def _file(self,name:str)->Path:
        relative=validated_path(name)
        target=self.root/relative
        if not target.resolve().is_relative_to(self.root):
            raise BulkProtocolError('Fichero fuera de la Memory Stick.')
        for candidate in (target,*target.parents):
            if candidate==self.root:
                break
            if candidate.is_symlink():
                raise BulkProtocolError('Enlaces no permitidos.')
        if not target.is_file() or target.stat().st_size>8*1024**3:
            raise BulkProtocolError('Archivo ausente o demasiado grande.')
        return target

    def reply(self,packet:bytes)->bytes:
        frame=Frame.decode(packet)
        if frame.op==Op.HELLO:
            if self.session is not None or frame.seq!=1 or frame.offset!=0:
                raise BulkProtocolError('La sesión ya se inició.')
            expected=frame.payload.decode('ascii','strict')
            if expected!=self.serial:
                raise BulkProtocolError('Serial Sony distinto: no iniciar.')
            self.session=frame.session
            self.next_seq=2
            result=json.dumps({'serial':self.serial,'read_only':True,
                               'max_chunk':MAX_PAYLOAD},sort_keys=True).encode()
            return Frame(Op.HELLO_ACK,self.session,1,result).encode()
        if (self.session is None or frame.session!=self.session or
                frame.seq!=self.next_seq):
            raise BulkProtocolError('Sesión PSP o secuencia USB distinta.')
        self.next_seq+=1
        if frame.op==Op.STAT:
            name=frame.payload.decode('utf-8','strict')
            target=self._file(name)
            h=hashlib.sha256()
            with target.open('rb') as stream:
                while block:=stream.read(1024*1024):
                    h.update(block)
            meta={'path':name,'size':target.stat().st_size,'sha256':h.hexdigest()}
            return Frame(Op.META,self.session,frame.seq,
                         json.dumps(meta,sort_keys=True).encode()).encode()
        if frame.op==Op.READ:
            if len(frame.payload)<4:
                raise BulkProtocolError('READ sin longitud.')
            count=struct.unpack_from('<I',frame.payload)[0]
            if not 0<count<=MAX_PAYLOAD:
                raise BulkProtocolError('Chunk USB fuera de límites.')
            name=frame.payload[4:].decode('utf-8','strict')
            target=self._file(name)
            with target.open('rb') as file:
                file.seek(frame.offset)
                payload=file.read(count)
            mode=Op.EOF if not payload else Op.DATA
            return Frame(mode,self.session,frame.seq,payload,
                         frame.offset).encode()
        raise BulkProtocolError('El servidor de solo lectura no acepta escrituras.')


class SimulatedHost:
    """PC-side only verified transfer via the synthetic PSP bulk transport."""

    def __init__(self,psp:InProcessPSPFileServer, *,session:bytes,
                 expected_serial:str):
        if len(session)!=16 or not any(session):
            raise BulkProtocolError('Sesión HID inválida.')
        self.psp=psp
        self.session=session
        self.seq=0
        response=self._exchange(Op.HELLO,expected_serial.encode('ascii'))
        if response.op!=Op.HELLO_ACK:
            raise BulkProtocolError('La PSP no confirmó el canal de archivos.')
        data=json.loads(response.payload)
        if data.get('serial')!=expected_serial or data.get('read_only') is not True:
            raise BulkProtocolError('No coincide el serial Sony o la propiedad de FAT.')

    def _exchange(self,op:Op,payload:bytes,offset:int=0)->Frame:
        self.seq+=1
        request=Frame(op,self.session,self.seq,payload,offset).encode()
        response=Frame.decode(self.psp.reply(request))
        if response.session!=self.session or response.seq!=self.seq:
            raise BulkProtocolError('La PSP respondió con otra sesión o secuencia.')
        return response

    def copy_game_readonly(self,name:str,destination:Path, *,
                           chunk_bytes:int=32*1024)->dict:
        validated_path(name)
        if not 1<=chunk_bytes<=MAX_PAYLOAD:
            raise BulkProtocolError('Tamaño de copia no válido.')
        meta_frame=self._exchange(Op.STAT,name.encode('utf-8'))
        if meta_frame.op!=Op.META:
            raise BulkProtocolError('No hay manifest verificado.')
        meta=json.loads(meta_frame.payload)
        if (meta.get('path')!=name or
                type(meta.get('size')) is not int or
                not 0<=meta['size']<=8*1024**3 or
                not re.fullmatch('[0-9a-f]{64}',meta.get('sha256',''))):
            raise BulkProtocolError('El manifiesto de juego no es fiable.')
        destination=Path(destination)
        if destination.exists() or destination.is_symlink():
            raise BulkProtocolError('No se sobrescribe un juego existente.')
        destination.parent.mkdir(parents=True,exist_ok=True)
        stage=destination.with_name('.pspdock-bulk-'+uuid.uuid4().hex+'.part')
        digest=hashlib.sha256()
        copied=0
        try:
            with stage.open('xb') as output:
                while copied<meta['size']:
                    count=min(chunk_bytes,meta['size']-copied)
                    payload=struct.pack('<I',count)+name.encode('utf-8')
                    response=self._exchange(Op.READ,payload,copied)
                    if (response.op!=Op.DATA or response.offset!=copied or
                            not 0<len(response.payload)<=count):
                        raise BulkProtocolError('Chunk de juego rechazado.')
                    output.write(response.payload)
                    digest.update(response.payload)
                    copied+=len(response.payload)
                output.flush()
                os.fsync(output.fileno())
            if copied!=meta['size'] or digest.hexdigest()!=meta['sha256']:
                raise BulkProtocolError('El juego no coincide con el SHA256 de PSP.')
            # Hard-link fails if another destination appears simultaneously.
            os.link(stage,destination)
        finally:
            stage.unlink(missing_ok=True)
        return {'bytes':copied,'sha256':meta['sha256'],
                'destination':str(destination),'physical_usb_proven':False}
