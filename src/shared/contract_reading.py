"""Contract boundary helpers; no business transition or approval logic."""

import logging
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from shared.contract_validation import load_validators, select_validator

Model = TypeVar("Model", bound=BaseModel)
logger = logging.getLogger(__name__)


def read_model(root: Path, schema: str, model: type[Model], payload: dict) -> Model:
    tolerant = select_validator(load_validators(root, tolerant=True), schema)
    tolerant.validate(payload)
    strict = select_validator(load_validators(root), schema)

    def log_unknown(error):
        if error.validator == "enum":
            logger.warning(
                "Unknown contract code at %s: %r", list(error.absolute_path), error.instance
            )
        for child in error.context:
            log_unknown(child)

    for error in strict.iter_errors(payload):
        log_unknown(error)
    return model.model_validate(payload)
