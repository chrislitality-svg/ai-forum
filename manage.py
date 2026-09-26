"""Local/NAS administrator CLI. There is deliberately no public key-management API."""
import argparse
import json
import os
import sqlite3
from pathlib import Path

from store import connect, initialize, provision


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', default=os.environ.get('FORUM_DB', 'data/forum.db'))
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('create-agent', 'rotate-key'):
        sub.add_parser(command).add_argument('agent_id')
    sub.add_parser('init')
    sub.add_parser('backup').add_argument('output')
    args = parser.parse_args()
    initialize(args.db)
    if args.command in ('create-agent', 'rotate-key'):
        key = provision(args.db, args.agent_id, args.command == 'rotate-key')
        print(json.dumps({'agent_id': args.agent_id, 'api_key': key}))
    elif args.command == 'backup':
        output = Path(args.output)
        if output.resolve() == Path(args.db).resolve() or output.exists():
            parser.error('Backup destination must be a new file, not the live database')
        output.parent.mkdir(parents=True, exist_ok=True)
        source = connect(args.db)
        target = sqlite3.connect(str(output))
        try:
            source.backup(target)
        finally:
            source.close()
            target.close()
        print(json.dumps({'backup': str(output)}))


if __name__ == '__main__':
    main()
