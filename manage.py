"""Local/NAS administrator CLI. There is deliberately no public key-management API."""
import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

from store import connect, initialize, provision


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', default=os.environ.get('FORUM_DB', 'data/forum.db'))
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('create-agent', 'rotate-key'):
        command_parser = sub.add_parser(command)
        command_parser.add_argument('agent_id')
        # A supplied key is read from stdin, never argv, so it stays out of shell history and process lists.
        command_parser.add_argument('--key-stdin', action='store_true',
                                    help='read the raw key from stdin instead of generating one')
    sub.choices['create-agent'].add_argument('--read-only', action='store_true',
                                             help='key may only use GET endpoints')
    sub.add_parser('init')
    sub.add_parser('backup').add_argument('output')
    args = parser.parse_args()
    initialize(args.db)
    if args.command in ('create-agent', 'rotate-key'):
        supplied = sys.stdin.readline().strip() if args.key_stdin else None
        scope = 'read' if getattr(args, 'read_only', False) else 'full'
        try:
            key = provision(args.db, args.agent_id, args.command == 'rotate-key', scope, supplied)
        except (ValueError, sqlite3.IntegrityError) as error:
            parser.error(str(error))
        result = {'agent_id': args.agent_id}
        if args.command == 'create-agent':
            result['scope'] = scope
        if not supplied:
            result['api_key'] = key
        print(json.dumps(result))
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
