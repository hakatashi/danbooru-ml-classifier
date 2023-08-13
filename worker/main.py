from firebase_functions import tasks_fn
from firebase_admin import initialize_app, firestore
from firebase_functions.options import RetryConfig, RateLimits
from typing import Any

app = initialize_app()

@tasks_fn.on_task_dispatched(
    rate_limits=RateLimits(
        max_concurrent_dispatches=6,
    ),
    retry_config=RetryConfig(
        max_attempts=5,
        max_backoff_seconds=60,
    ),
)
def executeTestFunction2(req: tasks_fn.CallableRequest) -> Any:
    print('invoked')
    db = firestore.client(app)
    artwork_id = req.data.get('artworkId')
    artwork_doc = db.document('artworks', artwork_id).get()
    print(artwork_doc.to_dict())