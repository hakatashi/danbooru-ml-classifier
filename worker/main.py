from firebase_functions import https_fn
from firebase_admin import initialize_app, storage
from io import BytesIO
from joblib import load
from deepdanbooru import pretrained_resnet50_model

initialize_app()

@https_fn.on_request()
def executeTestFunction4(req: https_fn.Request) -> https_fn.Response:
    bucket = storage.bucket()

    model_file = bucket.blob('models/sklearn-multiclass-linear-svc.joblib')
    model_bytes_io = BytesIO()
    model_file.download_to_file(model_bytes_io)
    model_bytes_io.seek(0)
    clf = load(model_bytes_io)
    print(clf)

    deepdanbooru_model = pretrained_resnet50_model()
    print(deepdanbooru_model)

    return https_fn.Response(f"Hello world")