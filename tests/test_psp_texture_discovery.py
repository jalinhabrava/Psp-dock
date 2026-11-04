import json
import unittest
from io import BytesIO

import psp_dock.emulation.psp_texture_discovery as discovery
from psp_dock.emulation.psp_texture_packs import TexturePackError


class _Response:
    def __init__(self,content,url=discovery.REMOTE_INDEX):
        self.content=BytesIO(content)
        self.url=url
    def __enter__(self):return self
    def __exit__(self,*unused):self.content.close()
    def geturl(self):return self.url
    def read(self,n):return self.content.read(n)


def catalog(*,serial='UCES-01421'):
    return json.dumps({
        'schemaVersion':1,'platform':'ppsspp',
        'entries':[{
            'id':'another-patapon3-pack',
            'name':'New independent HD textures for Patapon 3',
            'serials':[serial],
            'authors':['Community artist'],
            'sourceUrl':'https://github.com/independent/ppsspp-textures',
            'downloadUrl':'https://github.com/independent/ppsspp-textures/archive/main.zip',
            'sha256':'a'*64,
        }],
    }).encode()


class PublicTextureDiscoveryTests(unittest.TestCase):
    def setUp(self):
        discovery._cached=None
        discovery._last_attempt=0
    def tearDown(self):
        discovery._cached=None
        discovery._last_attempt=0

    def test_public_catalog_discovery_does_not_trust_unreviewed_download(self):
        def opener(request,timeout):
            self.assertEqual(request.full_url,discovery.REMOTE_INDEX)
            return _Response(catalog())
        records=discovery.read_remote_index(opener=opener)
        self.assertEqual(len(records),1)
        result=discovery.sources_online(
            'UCES01421',fetcher=lambda:records,clock=lambda:1000)
        new=next(x for x in result['sources'] if x['id']=='another-patapon3-pack')
        self.assertFalse(new['downloadable'])
        self.assertNotIn('download_url',new)
        self.assertIn('https://github.com/',new['source_url'])
        self.assertEqual(result['unverified_new'],1)

    def test_search_cache_avoids_repeated_network_calls_on_game_selection(self):
        requests=[]
        def load():
            requests.append(1)
            return discovery.read_remote_index(
                opener=lambda request,timeout:_Response(catalog()))
        discovery.sources_online('UCES01421',fetcher=load,clock=lambda:1)
        discovery.sources_online('UCES01421',fetcher=load,clock=lambda:2)
        self.assertEqual(len(requests),1)
        discovery.sources_online('UCES01421',fetcher=load,clock=lambda:9000)
        self.assertEqual(len(requests),2)

    def test_redirect_and_oversized_catalog_are_rejected(self):
        with self.assertRaises(TexturePackError):
            discovery.read_remote_index(opener=lambda request,timeout:
                _Response(catalog(),url='https://raw.githubusercontent.com/other/repo'))
        with self.assertRaises(TexturePackError):
            discovery.read_remote_index(opener=lambda request,timeout:
                _Response(b'A'*(discovery.MAX_INDEX_BYTES+1)))

    def test_wrong_platform_or_suspicious_sources_not_listed(self):
        d=json.loads(catalog())
        d['entries'][0]['sourceUrl']='https://127.0.0.1/a.zip'
        self.assertEqual(discovery.read_remote_index(
            opener=lambda request,timeout:_Response(json.dumps(d).encode())),[])
        d['platform']='duckstation'
        with self.assertRaises(TexturePackError):
            discovery.read_remote_index(
                opener=lambda request,timeout:_Response(json.dumps(d).encode()))


if __name__=="__main__":
    unittest.main()
