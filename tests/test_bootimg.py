import gzip
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from nabu_bootimg import verify


class AndroidBootImage(unittest.TestCase):
    def fixture(self):
        binary=b'compiled U-Boot firmware'*100
        dtb=b'compiled Nabu DTB'
        payload=gzip.compress(binary,mtime=0)+dtb
        page=4096
        data=bytearray(page+((len(payload)+page-1)//page)*page)
        data[:8]=b'ANDROID!'
        struct.pack_into('<II',data,8,len(payload),0x8000)
        struct.pack_into('<I',data,32,0x100)
        struct.pack_into('<I',data,36,page)
        data[page:page+len(payload)]=payload
        return data,binary,dtb

    def test_exact_binary_and_dtb_are_bound_to_wrapper(self):
        image,binary,dtb=self.fixture()
        result=verify(image,binary,dtb)
        self.assertEqual(result['header_version'],0)
        self.assertEqual(result['page_size'],4096)
        self.assertEqual(result['kernel_address'],0x8000)
        for wrong_binary,wrong_dtb in ((binary+b'x',dtb),(binary,dtb+b'x')):
            with self.assertRaises(ValueError): verify(image,wrong_binary,wrong_dtb)

    def test_wrong_wrapper_and_truncation_are_rejected(self):
        for offset,value in ((12,0x10000),(16,1),(20,1),(24,1),(28,1),
                             (32,0),(36,2048),(40,2),(44,1)):
            image,binary,dtb=self.fixture()
            struct.pack_into('<I',image,offset,value)
            with self.subTest(offset=offset),self.assertRaises(ValueError):
                verify(image,binary,dtb)
        image,binary,dtb=self.fixture()
        with self.assertRaises(ValueError): verify(image[:-1],binary,dtb)

    def test_corrupt_compressed_payload_is_rejected(self):
        image,binary,dtb=self.fixture()
        image[4096:4100]=b'bad!'
        with self.assertRaisesRegex(ValueError,'gzip payload'):
            verify(image,binary,dtb)


if __name__=='__main__': unittest.main()
