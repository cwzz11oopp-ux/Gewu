from fastapi import HTTPException


def not_found(name: str, value: str) -> HTTPException:
    return HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": f"{name} {value} was not found."})
