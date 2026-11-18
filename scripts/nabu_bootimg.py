"""Verify the Android v0 wrapper used by the pinned Nabu U-Boot recipe."""
import argparse
import json
from pathlib import Path
import struct
import zlib


def verify(image,binary,dtb,page_size=4096):
    if len(image)<48 or image[:8]!=b'ANDROID!':
        raise ValueError('Not an Android boot image')
    size=struct.unpack_from('<I',image,8)[0]
    page=struct.unpack_from('<I',image,36)[0]
    header=struct.unpack_from('<I',image,40)[0]
    if header!=0 or page!=page_size or not size:
        raise ValueError('Unexpected Android header/page size')
    if struct.unpack_from('<I',image,16)[0] or struct.unpack_from('<I',image,24)[0]:
        raise ValueError('Unexpected ramdisk/second-stage payload')
    # Match the reviewed Nabu recipe: base zero, kernel offset 0x8000,
    # no ramdisk/second-stage addresses, tags offset 0x100 and no OS version.
    for offset,expected in ((12,0x8000),(20,0),(28,0),(32,0x100),(44,0)):
        if struct.unpack_from('<I',image,offset)[0]!=expected:
            raise ValueError('Unexpected Android load address/version')
    if len(image)!=page+((size+page-1)//page)*page:
        raise ValueError('Truncated or trailing Android image data')
    payload=image[page:page+size]
    stream=zlib.decompressobj(31)
    try:
        unpacked=stream.decompress(payload,len(binary)+1)
    except zlib.error as error:
        raise ValueError('Invalid U-Boot gzip payload') from error
    if not stream.eof or unpacked!=binary:
        raise ValueError('Wrapped binary differs from the compiled U-Boot')
    if stream.unused_data!=dtb:
        raise ValueError('Appended DTB differs from the compiled Nabu DTB')
    return {'header_version':header,'page_size':page,'payload_bytes':size,
            'kernel_address':struct.unpack_from('<I',image,12)[0],
            'binary_bytes':len(binary),'dtb_bytes':len(dtb)}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('image','binary','dtb'):
        parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    try:
        print(json.dumps(verify(args.image.read_bytes(),args.binary.read_bytes(),args.dtb.read_bytes()),indent=2))
    except (OSError,ValueError) as error:
        raise SystemExit('Nabu boot image rejected: '+str(error))
