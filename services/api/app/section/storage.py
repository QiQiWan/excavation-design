from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile

from app.section.models import FrozenSectionSnapshot, canonical_json
from app.section.service import validate_result


class SectionStore:
    """Content-addressed immutable records; independent of Project adoption."""

    def __init__(self, root: Path):
        self.root = Path(root)

    @staticmethod
    def _identity(value: str) -> str:
        if not isinstance(value, str) or re.fullmatch('[0-9a-f]{64}', value) is None:
            raise ValueError('Invalid section content identity')
        return value

    def _path(self, group: str, identity: str) -> Path:
        return self.root / group / f'{self._identity(identity)}.json'

    def _save(self, path: Path, value: dict) -> None:
        encoded = canonical_json(value).encode('utf-8')
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() != encoded:
                raise ValueError('Existing immutable record differs; refusing overwrite')
            return
        descriptor, temporary = tempfile.mkstemp(prefix='.section-', dir=path.parent)
        try:
            with os.fdopen(descriptor, 'wb') as output:
                output.write(encoded)
                output.flush()
                os.fsync(output.fileno())
            # Link the completed file with exclusive creation. Two identical
            # computations may race; neither may overwrite a previous record.
            try:
                os.link(temporary, path)
            except FileExistsError:
                if path.read_bytes() != encoded:
                    raise ValueError('Concurrent immutable record conflict')
        finally:
            Path(temporary).unlink(missing_ok=True)

    def save_snapshot(self, snapshot: FrozenSectionSnapshot) -> None:
        self._save(self._path('snapshots', snapshot.snapshot_hash), snapshot.to_record())

    def load_snapshot(self, snapshot_hash: str) -> FrozenSectionSnapshot:
        record = json.loads(self._path('snapshots', snapshot_hash).read_text(encoding='utf-8'))
        snapshot = FrozenSectionSnapshot.from_record(record)
        if snapshot.snapshot_hash != snapshot_hash:
            raise ValueError('Stored snapshot identity mismatch')
        return snapshot

    def save_result(self, snapshot: FrozenSectionSnapshot, result: dict) -> None:
        validate_result(snapshot, result)
        if self.load_snapshot(snapshot.snapshot_hash) != snapshot:
            raise ValueError('Result source snapshot is not the stored immutable input')
        self._save(self._path('results', result['resultHash']), result)
        index = self.root / 'snapshot_results' / self._identity(snapshot.snapshot_hash) / f'{self._identity(result["resultHash"])}.json'
        self._save(index, {'snapshotHash': snapshot.snapshot_hash, 'resultHash': result['resultHash']})

    def load_result(self, result_hash: str) -> dict:
        result = json.loads(self._path('results', result_hash).read_text(encoding='utf-8'))
        if result.get('resultHash') != result_hash:
            raise ValueError('Stored result identity mismatch')
        snapshot = self.load_snapshot(result['snapshotHash'])
        validate_result(snapshot, result)
        return result

    def export_directory(self, result_hash: str) -> Path:
        return self.root / 'packages' / self._identity(result_hash)

    def save_reference_contract(self, contract: dict) -> None:
        from .reference_contract import validate_reference_contract
        validate_reference_contract(contract)
        self._save(self._path('reference_contracts', contract['contractHash']), contract)

    def load_reference_contract(self, identity: str) -> dict:
        from .reference_contract import validate_reference_contract
        contract = json.loads(self._path('reference_contracts', identity).read_text(encoding='utf-8'))
        validate_reference_contract(contract)
        if contract['contractHash'] != identity:
            raise ValueError('Stored reference contract identity mismatch')
        return contract

    def save_reference_curves(self, bundle: dict) -> None:
        from .reference_curves import validate_curve_bundle
        validate_curve_bundle(bundle)
        self._save(self._path('reference_curves', bundle['bundleHash']), bundle)

    def load_reference_curves(self, identity: str) -> dict:
        from .reference_curves import validate_curve_bundle
        bundle = json.loads(self._path('reference_curves', identity).read_text(encoding='utf-8'))
        validate_curve_bundle(bundle)
        if bundle['bundleHash'] != identity:
            raise ValueError('Stored reference curve identity mismatch')
        return bundle

    def _validate_comparison(self, record):
        from .reference_workflow import validate_reference_comparison
        result = self.load_result(record['resultHash'])
        snapshot = self.load_snapshot(record['snapshotHash'])
        contract = self.load_reference_contract(record['contractHash'])
        curves = self.load_reference_curves(record['curveBundleHash'])
        validate_reference_comparison(record, snapshot, result, contract, curves)

    def save_reference_comparison(self, record: dict) -> None:
        try:
            self._validate_comparison(record)
        except (FileNotFoundError, KeyError, TypeError) as exc:
            raise ValueError('Reference comparison evidence is missing or mismatched') from exc
        self._save(self._path('reference_comparisons', record['comparisonHash']), record)
        index = self.root / 'result_comparisons' / self._identity(record['resultHash']) / f'{self._identity(record["comparisonHash"])}.json'
        self._save(index, {'resultHash': record['resultHash'], 'comparisonHash': record['comparisonHash']})

    def load_reference_comparison(self, identity: str) -> dict:
        record = json.loads(self._path('reference_comparisons', identity).read_text(encoding='utf-8'))
        if record['comparisonHash'] != identity:
            raise ValueError('Stored reference comparison identity mismatch')
        self._validate_comparison(record)
        return record

    def load_latest_reference_comparison(self, result_hash: str) -> dict:
        directory = self.root / 'result_comparisons' / self._identity(result_hash)
        records = list(directory.glob('*.json'))
        if not records:
            raise FileNotFoundError('No reference comparison has been saved for this result')
        # FileNotFoundError means "no comparison exists" only above. Once an
        # index exists, loss of the record or any of its immutable dependencies
        # is corrupt/incomplete evidence, not permission to export less of it.
        try:
            latest = max(records, key=lambda path: (path.stat().st_mtime_ns, path.name))
            index = json.loads(latest.read_text(encoding='utf-8'))
            record = self.load_reference_comparison(index['comparisonHash'])
            if record['resultHash'] != result_hash or index['resultHash'] != result_hash:
                raise ValueError('Stored comparison recovery index mismatch')
            return record
        except (FileNotFoundError, KeyError, TypeError) as exc:
            raise ValueError('Saved reference comparison has missing or damaged dependency evidence') from exc

    def reference_supplement(self, snapshot, result):
        from .reference_workflow import reference_supplement
        try:
            record = self.load_latest_reference_comparison(result['resultHash'])
        except FileNotFoundError:
            contract_hash = snapshot.payload().get('reference_contract_hash')
            if not contract_hash:
                return None
            return reference_supplement(snapshot, result, self.load_reference_contract(contract_hash))
        return reference_supplement(snapshot, result, self.load_reference_contract(record['contractHash']),
                                    self.load_reference_curves(record['curveBundleHash']), record)

    def load_latest_result(self, snapshot_hash: str) -> dict:
        """Recover completed work by the already-known immutable input ID.

        Index records are created only after a complete sealed result is
        durable. Recovery never treats an in-progress calculation as finished.
        """
        self.load_snapshot(snapshot_hash)
        directory = self.root / 'snapshot_results' / self._identity(snapshot_hash)
        records = list(directory.glob('*.json'))
        if not records:
            raise FileNotFoundError('No completed result exists for this snapshot')
        latest = max(records, key=lambda path: (path.stat().st_mtime_ns, path.name))
        index = json.loads(latest.read_text(encoding='utf-8'))
        result = self.load_result(index['resultHash'])
        if index['snapshotHash'] != snapshot_hash or result['snapshotHash'] != snapshot_hash:
            raise ValueError('Stored recovery index refers to a different snapshot')
        return result


def get_section_store() -> SectionStore:
    default = Path(__file__).resolve().parents[2] / 'exports' / 'frozen_sections'
    return SectionStore(Path(os.environ.get('PITGUARD_SECTION_STORE', str(default))))
