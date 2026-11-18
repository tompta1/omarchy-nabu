"""Keep OS source pins separate from independently built bootloader inputs."""
import json
from pathlib import Path
import re


def load_sources(path):
    sources=json.loads(Path(path).read_text())
    for name,source in sources.items():
        if (not re.fullmatch(r'[a-z0-9][a-z0-9-]*',name)
                or source.get('scope','os') not in ('os','bootloader')
                or not re.fullmatch(r'[0-9a-f]{40}',source['commit'])
                or not source['url'].startswith('https://')):
            raise ValueError('Invalid source pin: '+name)
    return sources


def scoped_sources(sources,scope='os'):
    if scope not in ('os','bootloader','all'):
        raise ValueError('Unsupported source scope')
    return {name:source for name,source in sources.items()
            if scope=='all' or source.get('scope','os')==scope}


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file',type=Path,default=Path(__file__).resolve().parents[1]/'sources.json')
    parser.add_argument('--scope',choices=('os','bootloader','all'),default='os')
    args=parser.parse_args()
    print(json.dumps(scoped_sources(load_sources(args.file),args.scope),indent=2))
