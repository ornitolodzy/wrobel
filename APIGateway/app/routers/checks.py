from fastapi import APIRouter, Depends, HTTPException, Path, status

from app.db import Database
from app.deps import Client, get_channel, get_db
from app.schemas import CheckResponse, DeleteResponse

router = APIRouter(prefix="/api/v1/checks", tags=["checks"])

_ID = Path(..., pattern=r"^[0-9a-f-]{36}$")


async def _get_existing(check_id: str, db: Database):
    check = await db.get_check(check_id)
    if check is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Nie znaleziono sprawdzenia.")
    return check


@router.get("/{check_id}", response_model=CheckResponse, summary="Zwraca zapis przebiegu sprawdzenia")
async def get_check(
    check_id: str = _ID,
    db: Database = Depends(get_db),
) -> CheckResponse:
    check = await _get_existing(check_id, db)
    return CheckResponse.model_validate(check.response)


@router.delete("/{check_id}", response_model=DeleteResponse, summary="Usuwa zapis sprawdzenia")
async def delete_check(
    check_id: str = _ID,
    client: Client = Depends(get_channel),
    db: Database = Depends(get_db),
) -> DeleteResponse:
    await _get_existing(check_id, db)
    deleted = await db.delete_check(check_id)
    await db.log_event("check_deleted", client=client.name, check_id=check_id)
    return DeleteResponse(check_id=check_id, deleted=deleted)
