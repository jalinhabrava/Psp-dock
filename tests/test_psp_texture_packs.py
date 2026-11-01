from contextlib import contextmanager
from dataclasses import replace
import hashlib
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
import zipfile

from psp_texture_packs import (
    TextureOffer, TexturePackError, inspect_zip, download_and_install,
    validated_url, offers_for_disc,
)


def zip_fixture(items=None):
    out=BytesIO()
    with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_STORED) as arc:
        for k,v in (items or {'textures.ini':b'[options]\nversion=1\n',
                              'ui/menu.png':b'\x89PNG\r\n\x1a\nFAKE_TEXTURE_TEST'}).items():
            arc.writestr(k,v)
    return out.getvalue()


class FakeResponse:
    def __init__(self,content,url):
        self.buffer=BytesIO(content)
        self.url=url
    def __enter__(self):return self
    def __exit__(self,*args):self.buffer.close()
    def geturl(self):return self.url
    def read(self,n):return self.buffer.read(n)


class PSPTexturePackTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.home=Path(self.tmp.name)
        self.memstick=self.home/'PC/PSP'
        self.memstick.mkdir(parents=True)
        self.blob=zip_fixture()
        self.url='https://github.com/test-author/test-pack/releases/download/v1/pack.zip'
        self.offer=TextureOffer(
            id='test-pack',disc_id='ULJM05558',title='Test synthetic pack',
            publisher='Test author',source_url='https://github.com/test-author/test-pack',
            archive_url=self.url,sha256=hashlib.sha256(self.blob).hexdigest(),
            size_bytes=len(self.blob),permission='Fixture only'
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_install_exact_disc_zip_hash_and_no_overwrites(self):
        def opener(request,timeout):
            self.assertEqual(request.full_url,self.url)
            return FakeResponse(self.blob,self.url)
        report=download_and_install(self.offer,matching_disc_id='ULJM05558',
                                    private_memstick=self.memstick,opener=opener)
        self.assertEqual(report['status'],'installed')
        path=self.memstick/'PSP/TEXTURES/ULJM05558/textures.zip'
        self.assertEqual(path.read_bytes(),self.blob)
        with self.assertRaisesRegex(TexturePackError,'Ya existe'):
            download_and_install(self.offer,matching_disc_id='ULJM05558',
                                 private_memstick=self.memstick,opener=opener)

    def test_bad_game_id_or_hash_never_installed(self):
        for offer,id in ((self.offer,'ULJM05559'),
                         (replace(self.offer,sha256='0'*64),'ULJM05558')):
            with self.subTest(id=id,hash=offer.sha256),self.assertRaises(TexturePackError):
                download_and_install(offer,matching_disc_id=id,
                                     private_memstick=self.memstick,
                                     opener=lambda req,timeout:FakeResponse(self.blob,self.url))
        self.assertFalse((self.memstick/'PSP/TEXTURES/ULJM05558/textures.zip').exists())

    def test_zip_slip_scripts_and_invalid_ini_are_rejected(self):
        for data in (
            zip_fixture({'textures.ini':b'[options]', '../evil.exe':b'BAD'}),
            zip_fixture({'textures.ini':b'[options]', 'run.exe':b'BAD'}),
            zip_fixture({'textures.ini':b'not a file', 'ui/menu.png':b'test'}),
            zip_fixture({'textures.ini':b'[options]', 'ui/texture.txt':b'test'}),
        ):
            path=self.home/'test.zip'
            path.write_bytes(data)
            with self.assertRaises(TexturePackError):
                inspect_zip(path)

    def test_redirects_to_local_ip_or_insecure_urls_are_rejected(self):
        for url in ('http://github.com/a/b','https://127.0.0.1/p',
                    'https://github.com@localhost/test',
                    'file:///etc/passwd','https://api.example.net/a.zip'):
            with self.assertRaises(TexturePackError):
                validated_url(url)
        self.assertEqual(offers_for_disc('ULJM05558'),[])


if __name__=='__main__':
    unittest.main()
