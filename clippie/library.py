"""Removing a video and everything derived from it."""
from __future__ import annotations

from .db import session
from .models import Candidate, Export, GameEvent, Job, MediaAsset
from .storage import get_storage


class AssetNotFound(Exception):
    pass


def delete_asset(asset_id: str) -> dict:
    """Delete the asset, its candidates, events, exports, jobs and stored files.

    Jobs still running for the asset stop at their next progress update (see jobs.Cancelled) and
    clean up any files they wrote after this ran."""
    with session() as s:
        a = s.get(MediaAsset, asset_id)
        if a is None:
            raise AssetNotFound(asset_id)
        cand_ids = [c.id for c in s.query(Candidate).filter_by(asset_id=asset_id).all()]
        export_ids = [e.id for e in s.query(Export).filter(Export.candidate_id.in_(cand_ids)).all()] if cand_ids else []
        if export_ids:
            s.query(Export).filter(Export.id.in_(export_ids)).delete(synchronize_session=False)
        s.query(GameEvent).filter_by(asset_id=asset_id).delete(synchronize_session=False)
        s.query(Job).filter_by(asset_id=asset_id).delete(synchronize_session=False)
        s.delete(a)  # candidates cascade
        s.commit()

    st = get_storage()
    for key in [f"sources/{asset_id}", f"thumbs/{asset_id}.jpg", f"analysis/{asset_id}"] \
            + [f"frames/{c}" for c in cand_ids] + [f"previews/{c}.mp4" for c in cand_ids] + [f"exports/{e}" for e in export_ids]:
        try:
            st.delete(key)
        except OSError:
            pass  # a still-running job holds the file; it purges it when it stops
    return {"candidates": len(cand_ids), "exports": len(export_ids)}
