"""PC-only PPSSPP display/GPU profiling for PSP Dock (Windows).

No firmware writes, no driver tweaks, no shader injection or DLSS claims.
The actual PSP geometry is 480x272 (~16:9). Full ultrawide field of view
requires a separately validated per-game hack; generic stretching DISTORTS.

Profiles apply ONLY to PSP Dock's private PC memstick/PSP/SYSTEM/ppsspp.ini
with an on-PC immutable backup and atomic publication. Every per-game change
is explicit; the automatic default is fit+resolution matched to the monitor.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import configparser
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import uuid
from functools import lru_cache


PSP_WIDTH, PSP_HEIGHT = 480, 272
QUALITY_MODES = ('auto', 'balanced', 'quality', 'ultra', 'manual')
ASPECT_MODES = ('fit', 'stretch')
BACKENDS = ('directx11', 'vulkan', 'gles')
TEXTURE_MODES = ('off', 'xbrz2', 'xbrz3', 'xbrz4', 'bicubic2')


class GraphicsError(ValueError):
    pass


@dataclass(frozen=True)
class Monitor:
    width: int
    height: int
    name: str = 'Pantalla principal'
    def __post_init__(self):
        if type(self.width) is not int or type(self.height) is not int or not (
                320 <= self.width <= 16384 and 240 <= self.height <= 8640):
            raise GraphicsError('Resolución de pantalla fuera del rango admitido.')

    @property
    def ratio(self) -> float:
        return self.width / self.height

    def snapshot(self):
        return {**asdict(self), 'ratio': round(self.ratio, 4),
                'ultrawide': self.ratio > 2.0}


def primary_monitor() -> Monitor:
    """Read current Windows primary display, not physical PSP LCD."""
    if os.name != 'nt':
        return Monitor(1920, 1080, 'Monitor genérico (simulación)')
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetSystemMetrics.restype = ctypes.c_int
    w, h = int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))
    return Monitor(w, h)


@lru_cache(maxsize=1)
def graphics_hardware() -> dict:
    """Descriptive hardware hint; never make unsupported feature claims."""
    result = {'gpu': 'Desconocida', 'nvidia_rtx': False,
              'native_dlss_in_ppsspp': False}
    if os.name != 'nt':
        return result
    try:
        process = subprocess.run(
            ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
             'Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name'],
            text=True, capture_output=True, timeout=12,
            creationflags=0x08000000,
        )
        names = [n.strip() for n in process.stdout.splitlines() if n.strip()]
        if process.returncode == 0 and names:
            preferred = next((name for name in names if 'NVIDIA' in name.upper()), names[0])
            result.update(gpu=preferred, nvidia_rtx='RTX' in preferred.upper())
    except (OSError, subprocess.SubprocessError):
        pass
    return result


def validate_choice(quality: str, aspect: str, backend: str = 'directx11',
                    texture: str = 'off'):
    if (quality not in QUALITY_MODES or aspect not in ASPECT_MODES or
            backend not in BACKENDS or texture not in TEXTURE_MODES):
        raise GraphicsError('Perfil gráfico, proporción o backend desconocidos.')


def recommend(monitor: Monitor, *, quality: str = 'auto',
              aspect: str = 'fit', backend: str = 'directx11',
              manual_scale: int = 3, texture: str = 'off') -> dict:
    validate_choice(quality, aspect, backend, texture)
    if type(manual_scale) is not int or not 1 <= manual_scale <= 10:
        raise GraphicsError('Escala manual fuera de rango.')
    # Choose according to SCREEN HEIGHT, not ultrawide width. For a 3440x1440
    # display, 5x=2400x1360 is close to full 1440p quality. 6x supersamples.
    baseline = min(10, max(1, round(monitor.height / PSP_HEIGHT)))
    scale = {
        'auto': baseline,
        'balanced': min(10, max(2, baseline - 1)),
        'quality': min(10, baseline + 1),
        'ultra': min(10, baseline + 2),
        'manual': manual_scale,
    }[quality]
    frame_width = min(monitor.width,
                      int(round(monitor.height * PSP_WIDTH / PSP_HEIGHT)))
    borders = max(0, (monitor.width - frame_width) // 2)
    if aspect == 'stretch':
        warning = (
            'Llenado completo por estiramiento: la imagen puede deformarse. '
            'No equivale a un parche de campo de visión ultrawide.'
        )
    elif monitor.ratio > 2:
        warning = (
            'Proporción original conservada con bandas laterales. Para llenar '
            '21:9 sin deformar se necesita un parche específico para el juego.'
        )
    else:
        warning = 'Relación de aspecto PSP conservada sin deformaciones.'
    return {
        'quality': quality, 'aspect': aspect, 'backend': backend,
        'texture': texture,
        'texture_upscale_algorithm': (
            'xBRZ' if texture.startswith('xbrz') else
            'Bicubic' if texture == 'bicubic2' else 'off'),
        'texture_upscale_on_cpu': texture != 'off',
        'monitor': monitor.snapshot(),
        'internal_resolution': scale,
        'internal_width': PSP_WIDTH * scale,
        'internal_height': PSP_HEIGHT * scale,
        'frame_width': monitor.width if aspect == 'stretch' else frame_width,
        'frame_height': monitor.height,
        'side_border_px': 0 if aspect == 'stretch' else borders,
        'true_ultrawide_fov': False,
        'gpu_ai_upscaling': False,
        'warning': warning,
    }


def preferences(path: Path) -> dict:
    path = Path(path)
    if not path.is_file():
        return {'default': {'quality': 'auto', 'aspect': 'fit', 'texture':'off'},
                'games': {}}
    obj = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(obj, dict) or not isinstance(obj.get('games'), dict):
        raise GraphicsError('Base de datos de perfiles gráficos ilegible.')
    default = obj.get('default') or {}
    validate_choice(default.get('quality', 'auto'), default.get('aspect','fit'),
                    texture=default.get('texture','off'))
    if len(obj['games']) > 2048:
        raise GraphicsError('Demasiados perfiles por juego.')
    for key, value in obj['games'].items():
        if (not isinstance(key,str) or not re.fullmatch('[0-9a-f]{24}',key)
                or not isinstance(value,dict)):
            raise GraphicsError('Identificador de perfil de juego inválido.')
        validate_choice(value.get('quality'),value.get('aspect'),
                        texture=value.get('texture','off'))
    return {'default': {'quality':default.get('quality','auto'),
                        'aspect':default.get('aspect','fit'),
                        'texture':default.get('texture','off')},
            'games':obj['games']}


def per_game_choice(db: dict, game_id: str | None) -> dict:
    if game_id and game_id in db['games']:
        return {**db['default'], **db['games'][game_id]}
    return dict(db['default'])


def atomic_json(path: Path, value: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp = path.with_name('.graphics-' + uuid.uuid4().hex + '.tmp')
    try:
        with tmp.open('x',encoding='utf-8') as file:
            json.dump(value,file,indent=2,ensure_ascii=False)
            file.flush()
            os.fsync(file.fileno())
        os.replace(tmp,path)
    finally:
        tmp.unlink(missing_ok=True)


def set_preference(path: Path, *, game_id: str | None,
                   quality: str, aspect: str, texture: str = 'off') -> dict:
    validate_choice(quality,aspect,texture=texture)
    obj = preferences(path)
    if game_id is None:
        obj['default'] = {'quality':quality,'aspect':aspect,'texture':texture}
    elif isinstance(game_id,str) and re.fullmatch('[0-9a-f]{24}',game_id):
        obj['games'][game_id] = {'quality':quality,'aspect':aspect,'texture':texture}
    else:
        raise GraphicsError('Identificador de juego inválido.')
    atomic_json(path,obj)
    return obj


def clear_preference(path: Path, *, game_id: str) -> dict:
    """Remove one game's own profile so it follows the default again."""
    if not isinstance(game_id,str) or not re.fullmatch('[0-9a-f]{24}',game_id):
        raise GraphicsError('Identificador de juego inválido.')
    obj = preferences(path)
    if obj['games'].pop(game_id, None) is not None:
        atomic_json(path,obj)
    return obj


def apply_private_ppsspp_ini(path: Path, *, plan: dict,
                             backup_dir: Path) -> dict:
    """Apply a known subset of 1.20.x settings to PC-only PPSSPP.

    Uses configparser to retain unrelated configuration values. Never touches
    controls.ini, game saves, PSP USB, global PPSSPP, NVIDIA driver settings,
    or per-game cheat files.
    """
    ini, backups = Path(path), Path(backup_dir)
    if (not ini.is_file() or ini.is_symlink() or
            ini.parent.is_symlink() or backups.is_symlink()):
        raise GraphicsError('El perfil PPSSPP privado no existe o no es seguro.')
    if not isinstance(plan,dict) or plan.get('aspect') not in ASPECT_MODES:
        raise GraphicsError('Plan gráfico inválido.')
    texture=plan.get('texture','off')
    if texture not in TEXTURE_MODES:
        raise GraphicsError('Escalado de texturas PPSSPP desconocido.')
    scale = plan.get('internal_resolution')
    if type(scale) is not int or not 1 <= scale <= 10:
        raise GraphicsError('Escalado PPSSPP inválido.')
    before = ini.read_bytes()
    if len(before) > 1024 * 1024 or b'\x00' in before:
        raise GraphicsError('ppsspp.ini del PC inválido.')
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    try:
        parser.read_string(before.decode('utf-8-sig'))
    except (UnicodeDecodeError,configparser.Error) as exc:
        raise GraphicsError('ppsspp.ini del PC no es reconocido.') from exc
    if not parser.has_section('Graphics'):
        parser.add_section('Graphics')
    if not parser.has_section('DisplayLayout.Landscape'):
        parser.add_section('DisplayLayout.Landscape')
    desired = (
        ('Graphics','InternalResolution',str(scale)),
        ('Graphics','FrameSkip','0'),
        ('Graphics','AutoFrameSkip','False'),
        ('Graphics','AnisotropyLevel','4'),
        ('Graphics','TexScalingLevel',
         {'off':'1','xbrz2':'2','xbrz3':'3','xbrz4':'4','bicubic2':'2'}[texture]),
        ('Graphics','TexScalingType',
         '2' if texture == 'bicubic2' else '0'),
        ('DisplayLayout.Landscape','DisplayStretch',
         'True' if plan['aspect']=='stretch' else 'False'),
    )
    # Enable PPSSPP's real HD texture replacement ONLY for a verified
    # installed pack. The texture-upscaling selector is a separate feature.
    # Never activate texture dumping or change the user's global PPSSPP.
    if plan.get('replace_textures') is True:
        desired += (('Graphics','ReplaceTextures','True'),)
    changed = False
    for section,key,value in desired:
        if parser.get(section,key,fallback=None) != value:
            parser.set(section,key,value)
            changed=True
    if not changed:
        return {'changed':False,'path':str(ini),'scale':scale}
    # Backup the original file ONCE by its content hash; never overwrite.
    oldhash=hashlib.sha256(before).hexdigest()
    backups.mkdir(parents=True,exist_ok=True)
    backup=backups/('ppsspp.before-graphics-'+oldhash[:24]+'.ini')
    if backup.is_file():
        if backup.read_bytes()!=before:
            raise GraphicsError('Backup PPSSPP contradictorio.')
    else:
        with backup.open('xb') as output:
            output.write(before)
            output.flush()
            os.fsync(output.fileno())
    import io
    buffer=io.StringIO()
    parser.write(buffer)
    after=buffer.getvalue().encode('utf-8')
    if ini.read_bytes()!=before:
        raise GraphicsError('ppsspp.ini cambió mientras se preparaba el perfil.')
    tmp=ini.with_name('.pspdock-graphics-'+uuid.uuid4().hex+'.tmp')
    try:
        with tmp.open('xb') as output:
            output.write(after)
            output.flush()
            os.fsync(output.fileno())
        if ini.read_bytes()!=before:
            raise GraphicsError('PPSSPP modificó el perfil antes de publicarlo.')
        os.replace(tmp,ini)
    finally:
        tmp.unlink(missing_ok=True)
    return {'changed':True,'path':str(ini),'backup':str(backup),'scale':scale}
