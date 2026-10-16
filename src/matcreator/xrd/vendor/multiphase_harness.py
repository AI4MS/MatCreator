"""Bounded combination search. LLM proposals and a genuine GP/EI control arm.

The optimizer can choose candidate IDs only. It cannot alter measured intensities,
the evaluator, the objective or candidate structures, nor execute generated code.
"""
from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field


class MultiphaseConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    owner_node_id: str = Field(default='', max_length=128)
    source_match_run_id: str = Field(default='', max_length=128)
    candidate_ids: list[str] = Field(default_factory=list, max_length=30)
    budget: int = Field(default=24, ge=6, le=100)
    max_phases: int = Field(default=3, ge=2, le=4)
    evaluate_baseline: bool = True
    seed: int = Field(default=42, ge=0, le=2147483647)
    refinement_max_nfev: int = Field(default=60, ge=20, le=500)
    pywpem_review: bool = True
    pywpem_iterations: int = Field(default=20, ge=1, le=100)
    optimizer_label: str = Field(default='LLM Agent', min_length=1, max_length=80)


class Proposal(BaseModel):
    model_config = ConfigDict(extra='forbid')
    candidate_ids: list[str] = Field(min_length=1, max_length=4)
    reason: str = Field(min_length=1, max_length=1600)


def validate_proposal(value, pool, max_phases, tried=()):
    if isinstance(value, str):
        text = value.strip()
        if text.startswith('```'):
            text = text.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
        value = json.loads(text)
    proposal = Proposal.model_validate(value)
    ids = proposal.candidate_ids
    if len(ids) != len(set(ids)):
        raise ValueError('A combination cannot contain a duplicate phase.')
    if len(ids) > max_phases or any(cid not in pool for cid in ids):
        raise ValueError('Choose only supplied candidate IDs within max_phases.')
    key = tuple(sorted(ids))
    if key in set(tried):
        raise ValueError('That combination has already been evaluated.')
    return list(key), proposal.reason
