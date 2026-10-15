"""Importing this package registers every model on Base.metadata."""

from app.models.ai import AiCall, AiScore
from app.models.application import Application, ApplicationEvent, EventKind, Stage
from app.models.company import Company
from app.models.cv import ApplicationCv, CvFile
from app.models.setting import Setting

__all__ = [
    "AiCall",
    "AiScore",
    "Application",
    "ApplicationCv",
    "ApplicationEvent",
    "Company",
    "CvFile",
    "EventKind",
    "Setting",
    "Stage",
]
