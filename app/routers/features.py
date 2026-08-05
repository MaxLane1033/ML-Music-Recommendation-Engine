from fastapi import APIRouter

from .. import recommender, schemas

router = APIRouter(prefix="/api/features", tags=["features"])


@router.get("", response_model=list[schemas.FeatureMeta])
def list_features():
    return recommender.feature_metadata()
