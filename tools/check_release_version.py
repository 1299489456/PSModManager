"""Reject mismatched/ambiguous release tags before dependency installation."""
import argparse
import ast
from pathlib import Path
import re
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def check(tag, root=ROOT):
    if not re.fullmatch(r'v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)', tag):
        raise ValueError('Release tag must be vMAJOR.MINOR.PATCH, for example v0.1.0')
    version = tomllib.loads((root/'pyproject.toml').read_text('utf-8'))['project']['version']
    identity = ast.parse((root/'backend/app/identity.py').read_text('utf-8'))
    app_version = next(ast.literal_eval(node.value) for node in identity.body
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name)
            and target.id == 'APP_VERSION' for target in node.targets))
    lock = tomllib.loads((root/'uv.lock').read_text('utf-8'))
    locked = next(row['version'] for row in lock['package'] if row['name'] == 'psmodmanager')
    if tag != 'v'+version or app_version != version or locked != version:
        raise ValueError(f'Version mismatch: tag={tag}, project={version}, app={app_version}, lock={locked}')
    return version


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag', required=True)
    print(check(parser.parse_args().tag))
