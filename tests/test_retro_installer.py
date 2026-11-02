"""Offline install source, archive, core allowlist and PC-only migration tests.

No HTTP request, game ROM, PSP drive, or real executable is used here.
"""
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from retro_installer import (
    RetroInstallError, _download, _check_url, _extract_core,
    install_plan, install_required, managed_retroarch, required_cores,
    STABLE_URL, CORE_BASE, STABLE_SHA256, AUTO_APPROVED_CORES,
)


def package(files):
    buffer=BytesIO()
    with zipfile.ZipFile(buffer,'w',compression=zipfile.ZIP_DEFLATED) as out:
        for name,data in files.items():
            out.writestr(name,data)
    return buffer.getvalue()


def emu(*systems):
    return {'name':'Synthetic PSP Emulator','systems':list(systems)}


class RetroInstallerUnitTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def test_unique_cores_for_detected_emulators_not_rom_or_bios(self):
        self.assertEqual(required_cores([emu('gba'),emu('gb','gbc'),emu('gba')]),
                         ['gambatte_libretro.dll','mgba_libretro.dll'])
        self.assertEqual(required_cores([emu('cps1','cps2','neogeo'),emu('arcade')]),
                         ['fbneo_libretro.dll','mame2003_plus_libretro.dll'])
        for invalid in ([emu('gba/../../evil')],[{'systems':['unknown_core']}],
                        [{'systems':'gba'}],[emu('gba')]*65):
            with self.subTest(invalid=invalid),self.assertRaises(RetroInstallError):
                required_cores(invalid)

    def test_commercial_safe_install_does_not_auto_fetch_noncommercial_cores(self):
        plan=install_plan(self.root,[emu('cps1','cps2','neogeo','arcade','gba',
                                          'snes','md','sms','gg','32x')])
        self.assertEqual(plan['requires_license_review'],
                         ['fbneo_libretro.dll','mame2003_plus_libretro.dll'])
        self.assertTrue({'bsnes_libretro.dll','blastem_libretro.dll',
                         'gearsystem_libretro.dll','mgba_libretro.dll'}
                        <= set(plan['auto_cores']))
        self.assertFalse(set(plan['auto_cores']) &
                         {'fbneo_libretro.dll','mame2003_plus_libretro.dll',
                          'snes9x_libretro.dll','picodrive_libretro.dll',
                          'genesis_plus_gx_libretro.dll'})
        self.assertTrue(set(plan['auto_cores'])<=AUTO_APPROVED_CORES)
        only_arcade=install_plan(self.root,[emu('cps1','neogeo','arcade')])
        self.assertEqual(only_arcade['status'],'license_review')
        self.assertFalse(only_arcade['needed'])
        with self.assertRaisesRegex(RetroInstallError,'licencias'):
            install_required(self.root,[emu('cps1','arcade')],
                             download=lambda *a,**kw:self.fail('network for restricted cores'))
        self.assertFalse((self.root/'emulators').exists())

    def test_installer_path_is_inside_selected_storage_not_psp(self):
        exe=managed_retroarch(self.root)
        self.assertEqual(exe,self.root/'emulators/retroarch/retroarch.exe')
        plan=install_plan(self.root,[emu('gba','gb')])
        self.assertTrue(plan['needed'])
        self.assertFalse(plan['retroarch_installed'])
        self.assertEqual(plan['missing_cores'],['gambatte_libretro.dll','mgba_libretro.dll'])
        self.assertEqual(install_plan(self.root,[])['status'],'not_detected')

    def test_external_retroarch_never_modified(self):
        other=self.root/'external/retroarch.exe'
        other.parent.mkdir(parents=True)
        other.write_bytes(b'Existing user executable')
        plan=install_plan(self.root,[emu('gba')],str(other))
        self.assertTrue(plan['external'])
        self.assertFalse(plan['needed'])
        with self.assertRaisesRegex(RetroInstallError,'externo|fuera'):
            install_required(self.root,[emu('gba')],configured=str(other),
                             download=lambda *args: self.fail('network call'))
        self.assertEqual(other.read_bytes(),b'Existing user executable')

    def test_https_source_locked_to_official_buildbot_domain(self):
        _check_url(STABLE_URL)
        _check_url(CORE_BASE+'mgba_libretro.dll.zip')
        for url in ('http://buildbot.libretro.com/file',
                    'https://buildbot.libretro.com.evil.test/core.zip',
                    'https://other.example/core.zip',
                    'https://buildbot.libretro.com@evil.test/core.zip',
                    'https://buildbot.libretro.com/core.zip?steal=1'):
            with self.subTest(url=url),self.assertRaises(RetroInstallError):
                _check_url(url)

    def test_download_detects_sha_and_refuses_truncation_or_wrong_domain(self):
        payload=b'archive-test-content'*20
        class Fake:
            def __init__(self, data, url=STABLE_URL, length=None):
                self.io=BytesIO(data)
                self.url=url
                self.headers={'Content-Length':str(len(data) if length is None else length)}
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,size):return self.io.read(size)
            def geturl(self):return self.url
        target=self.root/'archive'
        h=_download(STABLE_URL,target,10000,opener=lambda *_args,**_kwargs: Fake(payload))
        self.assertEqual(h,hashlib.sha256(payload).hexdigest())
        self.assertEqual(target.read_bytes(),payload)
        target.unlink()
        with self.assertRaisesRegex(RetroInstallError,'truncada'):
            _download(STABLE_URL,target,10000,
                      opener=lambda *_args,**_kwargs: Fake(payload,length=len(payload)+4))
        self.assertFalse(target.exists())
        with self.assertRaisesRegex(RetroInstallError,'servidor oficial'):
            _download(STABLE_URL,target,10000,
                      opener=lambda *_args,**_kwargs: Fake(payload,url='https://evil.test/archives'))
        self.assertFalse(target.exists())
        with self.assertRaisesRegex(RetroInstallError,'tamaño máximo'):
            _download(STABLE_URL,target,100,
                      opener=lambda *_args,**_kwargs: Fake(payload))

    def test_core_zip_only_exact_allowed_dll_and_no_zip_slip(self):
        target=self.root/'lib.dll'
        archive=self.root/'core.zip'
        archive.write_bytes(package({'mgba_libretro.dll':b'DLL_TEST_PAYLOAD'*30}))
        sha=_extract_core(archive,'mgba_libretro.dll',target)
        self.assertEqual(sha,hashlib.sha256(target.read_bytes()).hexdigest())
        target.unlink()
        for entries in ({'../mgba_libretro.dll':b'x'*1000},
                        {'evil.exe':b'x'*1000},
                        {'mgba_libretro.dll':b'x'*1000,'malicious.bat':b'x'*1000}):
            archive.write_bytes(package(entries))
            with self.assertRaises(RetroInstallError):
                _extract_core(archive,'mgba_libretro.dll',target)
            self.assertFalse(target.exists())

    def test_release_pin_is_fixed_and_official(self):
        self.assertEqual(len(STABLE_SHA256),64)
        self.assertEqual(STABLE_SHA256,
            'b2139b1d0f9d4526dc6b5ce23cbb3efdc766096fa6f2c3df016818b486ac6372')

    @unittest.skipUnless(os.name=='nt','Uses Windows bsdtar for the portable package')
    def test_offline_portable_install_and_new_core_only(self):
        retro=package({'RetroArch-Win64/retroarch.exe':b'SYNTHETIC-NOT-EXECUTABLE',
                       'RetroArch-Win64/retroarch.default.cfg':b'config=true'})
        core_mgba=package({'mgba_libretro.dll':b'mGBA-SYNTHETIC'*30})
        core_gambatte=package({'gambatte_libretro.dll':b'GAMBATTE-SYNTHETIC'*30})
        sources={STABLE_URL:retro,
                 CORE_BASE+'mgba_libretro.dll.zip':core_mgba,
                 CORE_BASE+'gambatte_libretro.dll.zip':core_gambatte}
        called=[]
        def fake_download(url,target,limit,cb):
            self.assertIn(url,sources)
            target.write_bytes(sources[url])
            called.append(url)
            cb(len(sources[url]),len(sources[url]))
            return hashlib.sha256(sources[url]).hexdigest()
        with patch('retro_installer.STABLE_SHA256',hashlib.sha256(retro).hexdigest()):
            report=install_required(self.root,[emu('gba')],download=fake_download)
            self.assertEqual(report['installed_cores'],['mgba_libretro.dll'])
            self.assertFalse(report['needed'])
            exe=managed_retroarch(self.root)
            self.assertEqual(exe.read_bytes(),b'SYNTHETIC-NOT-EXECUTABLE')
            result=install_required(self.root,[emu('gba','gb')],download=fake_download)
        self.assertEqual(result['installed_cores'],['gambatte_libretro.dll'])
        self.assertEqual(called.count(STABLE_URL),1)
        self.assertEqual(called.count(CORE_BASE+'mgba_libretro.dll.zip'),1)
        self.assertEqual(called.count(CORE_BASE+'gambatte_libretro.dll.zip'),1)
        manifest=json.loads((exe.parent/'psp-dock-install-manifest.json').read_text('utf-8'))
        self.assertEqual(set(manifest['cores']),{'mgba_libretro.dll','gambatte_libretro.dll'})
        self.assertFalse(list((self.root/'emulators').glob('.retro-install-*')))

    @unittest.skipUnless(os.name=='nt','Uses Windows bsdtar for the portable package')
    def test_wrong_sha_refuses_install_before_extract(self):
        fake=package({'RetroArch-Win64/retroarch.exe':b'FAKE',
                      'RetroArch-Win64/assets/README.txt':b'x'})
        def fake_download(url,target,limit,cb):
            target.write_bytes(fake)
            return hashlib.sha256(fake).hexdigest()
        with self.assertRaisesRegex(RetroInstallError,'SHA-256'):
            install_required(self.root,[emu('gba')],download=fake_download)
        self.assertFalse(managed_retroarch(self.root).exists())
        self.assertFalse(list((self.root/'emulators').glob('.retro-install-*')))


if __name__=='__main__':
    unittest.main()
