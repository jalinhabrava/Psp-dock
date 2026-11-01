import struct
import tempfile
import unittest
from pathlib import Path

import psp_game_metadata as m
from psp_artwork import SECTOR
from tests.test_psp_artwork import rec, write_fixture_cso, write_fixture_iso


def fixture_sfo(items=None):
    items=items or {"DISC_ID":"NPJH50053","TITLE":"Dariusburst",
                    "DISC_VERSION":"1.00","CATEGORY":"UG"}
    keys=bytearray()
    values=bytearray()
    entries=[]
    for name,value in items.items():
        key_offset=len(keys)
        keys+=name.encode("ascii")+b"\0"
        value_bytes=str(value).encode("utf-8")+b"\0"
        offset=len(values)
        values+=value_bytes
        entries.append(struct.pack("<HHIII",key_offset,0x0204,
                                   len(value_bytes),len(value_bytes),offset))
    key_start=20+16*len(entries)
    data_start=key_start+len(keys)
    header=struct.pack("<4sIIII",b"\0PSF",0x101,key_start,data_start,len(entries))
    return header+b"".join(entries)+keys+values


class PSPGameMetadataTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.home=Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_bounded_sfo_reads_known_game_id(self):
        data=fixture_sfo()
        self.assertEqual(m.parse_sfo(data)["DISC_ID"],"NPJH50053")
        self.assertEqual(m.parse_sfo(data)["TITLE"],"Dariusburst")
        self.assertFalse(m.parse_sfo(data[:18]))
        self.assertFalse(m.parse_sfo(data+b"\0"*(m.MAX_SFO_BYTES+1)))
        self.assertNotIn('DISC_ID',m.parse_sfo(fixture_sfo({"DISC_ID":"../outside"})))

    def test_pbp_reads_sfo_from_exact_first_section(self):
        pbp=self.home/'EBOOT.PBP'
        sfo=fixture_sfo()
        offsets=[40,40+len(sfo)]+[40+len(sfo)]*6
        pbp.write_bytes(b"\0PBP"+b"\0\0\1\0"+struct.pack("<8I",*offsets)+sfo)
        self.assertEqual(m.read_metadata(pbp)["DISC_ID"],"NPJH50053")
        pbp.write_bytes(b"INVALID"+pbp.read_bytes()[7:])
        self.assertEqual(m.read_metadata(pbp),{})

    def test_realistic_iso_and_cso_directory_metadata(self):
        iso=self.home/'game.iso'
        sfo=fixture_sfo()
        write_fixture_iso(iso)
        raw=bytearray(iso.read_bytes())
        entry=rec(b"PARAM.SFO;1",23,len(sfo),False)
        # ISO fixture places PSP_GAME directory at sector 21, ICON0 first.
        directory=21*SECTOR
        current=rec(b"ICON0.PNG;1",22,0,False)
        raw[directory+len(current):directory+len(current)+len(entry)]=entry
        raw[23*SECTOR:23*SECTOR+len(sfo)]=sfo
        iso.write_bytes(raw)
        self.assertEqual(m.read_metadata(iso)["TITLE"],"Dariusburst")
        cso=self.home/'game.cso'
        write_fixture_cso(cso,bytes(raw))
        self.assertEqual(m.read_metadata(cso)["DISC_ID"],"NPJH50053")


if __name__=='__main__':
    unittest.main()
