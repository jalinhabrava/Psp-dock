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
    validated_url, offers_for_disc, sources_for_disc, source_catalog,
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
        with self.assertRaisesRegex(TexturePackError,'Ya hay texturas'):
            download_and_install(self.offer,matching_disc_id='ULJM05558',
                                 private_memstick=self.memstick,opener=opener)

    def test_download_progress_and_existing_manual_pack_are_respected(self):
        statuses=[]
        report=download_and_install(
            self.offer,matching_disc_id='ULJM05558',
            private_memstick=self.memstick,
            opener=lambda request,timeout: FakeResponse(self.blob,self.url),
            progress=lambda current,total,phase:statuses.append((current,total,phase)),
        )
        self.assertEqual(report['status'],'installed')
        self.assertEqual(statuses[0],(0,len(self.blob),'downloading'))
        self.assertIn((len(self.blob),len(self.blob),'validating'),statuses)
        self.assertEqual(statuses[-1],(len(self.blob),len(self.blob),'installed'))
        (self.memstick/'PSP/TEXTURES/ULJM05558/textures.zip').unlink()
        manual=(self.memstick/'PSP/TEXTURES/ULJM05558/textures.ini')
        manual.write_text('[options]\nversion=1')
        with self.assertRaisesRegex(TexturePackError,'Ya hay texturas'):
            download_and_install(
                self.offer,matching_disc_id='ULJM05558',
                private_memstick=self.memstick,
                opener=lambda request,timeout: FakeResponse(self.blob,self.url),
            )
        self.assertEqual(manual.read_text(),'[options]\nversion=1')

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

    def test_exact_inert_source_file_in_known_lcs_pack_is_bounded(self):
        data=zip_fixture({
            'textures.ini':b'[options]\nversion=1\n',
            'Map/Radar.png':b'PNG_TEST',
            'Map/Map_Radar_Redraw.pdn':b'PAINTNET_TEST_DATA',
        })
        path=self.home/'lcs-synthetic.zip'
        path.write_bytes(data)
        self.assertEqual(inspect_zip(path)['textures'],1)
        data=zip_fixture({
            'textures.ini':b'[options]\nversion=1\n',
            'Map/Radar.png':b'PNG_TEST',
            'Map/Unknown_Radar.pdn':b'UNKNOWN_DATA',
        })
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

    def test_pinned_catalog_has_real_downloadable_packs_and_source_only_games(self):
        self.assertEqual(len(source_catalog()),23)
        vcs=sources_for_disc('ULUS10160')
        self.assertTrue(any(x['downloadable'] and x['publisher']=='SonofUgly'
                            for x in vcs))
        self.assertTrue(any(o['id'].startswith('sonofugly-')
                            for o in offers_for_disc('ULUS10160')))
        self.assertTrue(any(item['sha256'] ==
                            'b87686eb02a2e2db165d1aa91c09351ef92e41673313b3c47faa332fb012c2d4'
                            for item in source_catalog()))
        p2=sources_for_disc('UCES01177')
        self.assertTrue(any(x['publisher']=='shockturtle' and
                            not x['downloadable'] for x in p2))
        p3=sources_for_disc('UCES01421')
        self.assertTrue(any('Patapon 3' in x['title'] and
                            not x['downloadable'] for x in p3))
        self.assertEqual(sources_for_disc('ULJM05558'),[])
        # The catalog is factual metadata only, never copyrighted images.
        self.assertTrue(all('archive_bytes' not in x for x in source_catalog()))


if __name__=='__main__':
    unittest.main()
