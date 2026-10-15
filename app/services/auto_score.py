"""Background AI scoring after a CV is attached or the JD text changes.

The request that triggered it returns right away; the score runs afterwards
in a worker thread with its own database session (FastAPI BackgroundTasks).
While it runs, the cache key sits in an in-memory set, so the score panel can
say "scoring" and poll until the result is in.

Rules:
- Only when AI is configured and the `ai.auto_score` setting is on.
- Never when a result for that exact CV + JD + model + prompt is already cached,
  or when the same pair is already being scored. Manual Re-score is unaffected.
- A failure is logged like any other call and shows on the page. There is no
  automatic retry loop; the Score with AI button is the retry.

The in-flight set lives in memory: if the app stops mid-score, the pair simply
shows as pending again.
"""

import logging
import threading
from collections.abc import Callable

from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.client import AiClient
from app.models import AiScore, Application, CvFile
from app.services.ai_scores import cache_key, input_problem, run_score
from app.services.settings import ai_auto_score

log = logging.getLogger(__name__)


class AutoScorer:
    def __init__(self, session_factory: Callable[[], Session], client: AiClient) -> None:
        self._session_factory = session_factory
        self._client = client
        self._lock = threading.Lock()
        self._running: set[str] = set()

    def running_keys(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._running)

    def schedule(
        self, session: Session, background: BackgroundTasks, application: Application, cv_file: CvFile
    ) -> bool:
        """Queue a background score for this pair if needed. Returns True if one was queued.

        Call after committing, so the worker's session sees the new rows.
        """
        if not ai_auto_score(session) or input_problem(application, cv_file):
            return False
        key = cache_key(cv_file.text, application.jd_text, self._client.model)
        if session.scalar(select(AiScore.id).where(AiScore.cache_key == key)) is not None:
            return False
        with self._lock:
            if key in self._running:
                return False
            self._running.add(key)
        background.add_task(self._run, application.id, cv_file.id, key)
        return True

    def _run(self, application_id: int, cv_file_id: int, key: str) -> None:
        try:
            with self._session_factory() as session:
                application = session.get(Application, application_id)
                cv_file = session.get(CvFile, cv_file_id)
                if application is None or cv_file is None:
                    return
                # The JD or CV may have changed between scheduling and now.
                if cache_key(cv_file.text, application.jd_text, self._client.model) != key:
                    return
                run_score(session, self._client, application, cv_file)
                session.commit()
        except Exception:  # never let a background job take the app down
            log.exception("Background AI score failed for application %s", application_id)
        finally:
            with self._lock:
                self._running.discard(key)
