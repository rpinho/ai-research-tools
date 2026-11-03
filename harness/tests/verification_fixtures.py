"""Generate SYNTHETIC evidence in disposable directories; no real lane packets.

Lean filenames, statements, dependency pins and receipt output are invented.
No Lean build is run: these receipts exercise schema and mutation checks only.
"""
import json
import sys

import verification as v

MAINS = ['ToyProof.first', 'ToyProof.second', 'ToyProof.third', 'ToyProof.fourth']


def save_json(path, data):
    path.write_text(json.dumps(data, indent=2) + '\n')
    return v.sha256(path)


def reseal_lean(root, row):
    """Synthetic negative tests deliberately reseal mutations to test content rules."""
    receipt = dict(command=['lake', 'build'], exit_code=0, source_sha256=v.source_hash(root),
                   output='receipts/clean-build.log', output_sha256=v.sha256(root / 'receipts/clean-build.log'),
                   axioms_sha256=v.sha256(root / 'receipts/axioms.txt'),
                   axioms_md_sha256=v.sha256(root / 'AXIOMS.md'))
    row['build_receipt_sha256'] = save_json(root / 'receipts/build.json', receipt)
    return row


def synthetic_lean(root):
    """Create a toy six-source replay packet with freshly computed toy hashes."""
    root.mkdir(parents=True, exist_ok=True)
    (root / 'ToyProof').mkdir()
    (root / 'receipts').mkdir()
    (root / 'lean-toolchain').write_text('leanprover/lean4:v4.0.0\n')
    save_json(root / 'lake-manifest.json', {
        'packages': [{'name': 'toy_dependency', 'type': 'git', 'rev': 'a' * 40}],
    })
    (root / 'lakefile.toml').write_text('name = "ToyProof"\n')
    modules = ['First', 'Second', 'Third', 'Fourth']
    for module, theorem in zip(modules, MAINS):
        (root / 'ToyProof' / (module + '.lean')).write_text(
            '-- SYNTHETIC source for validator tests only\n'
            'theorem ' + theorem + ' : True := True.intro\n')
    (root / 'ToyProof.lean').write_text(''.join('import ToyProof.' + n + '\n' for n in modules))
    (root / 'Audit.lean').write_text('import ToyProof\n' + ''.join('#print axioms ' + n + '\n' for n in MAINS))
    (root / 'AXIOMS.md').write_text(
        '# Synthetic axiom audit\n\nNon-standard axiom list: **empty**.\n'
        'No source uses native_decide.\n')
    audit = ''.join("'" + n + "' depends on axioms: [propext, Quot.sound]\n" for n in MAINS)
    (root / 'receipts/axioms.txt').write_text(audit)
    (root / 'receipts/clean-build.log').write_text(
        'SYNTHETIC receipt; no Lean build was run.\n' + audit +
        'Build completed successfully.\nEXIT_CODE=0\n')
    row = dict(verification='L2', finite_component='none',
               verification_scope='synthetic toy statements for validator tests only',
               lean_project='.', build_receipt='receipts/build.json',
               axioms_receipt='receipts/axioms.txt', main_theorems=list(MAINS))
    return reseal_lean(root, row)


def l1(root, seconds=5):
    root.mkdir(parents=True, exist_ok=True)
    (root / 'certificate.txt').write_text('2\n')
    (root / 'checker.py').write_text("from pathlib import Path\nassert int(Path('certificate.txt').read_text()) == 2\nprint('exact: 2')\n")
    (root / 'output.txt').write_text('exact: 2\n')
    row = dict(verification='L1', finite_component='none', verification_scope='finite witness equals 2',
               producer_author='model-producer', checker_author='model-independent', certificate='certificate.txt',
               checker=[sys.executable, 'checker.py'], checker_max_seconds=seconds,
               expected_output_sha256=v.sha256(root / 'output.txt'), run_receipt='run.json')
    receipt = dict(command=row['checker'], exit_code=0, producer_author=row['producer_author'],
                   checker_author=row['checker_author'], certificate_sha256=v.sha256(root / 'certificate.txt'),
                   output='output.txt', output_sha256=row['expected_output_sha256'],
                   checker_sources_sha256={'checker.py': v.sha256(root / 'checker.py')})
    row['run_receipt_sha256'] = save_json(root / 'run.json', receipt)
    return row
