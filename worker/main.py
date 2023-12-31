from firebase_functions import firestore_fn, options
from firebase_admin import initialize_app, storage, firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from io import BytesIO
from joblib import load
from PIL import Image, UnidentifiedImageError
import torch
from torchvision import models
from danbooru_resnet import _resnet
from tagger import get_raw_tags
from downloader import download_images
from torch_network import get_torch_network
import json
from urllib.request import urlopen
import os

initialize_app()

preference_linear_svc_model = None
preference_ada_boost_model = None
preference_torch_network_model = None
deepdanbooru_model = None
class_names = None

def get_rss():
    import resource
    rusage = resource.getrusage(resource.RUSAGE_SELF)
    return rusage.ru_maxrss

def get_preference_linear_svc_model():
    bucket = storage.bucket('danbooru-ml-classifier')
    model_file = bucket.blob('preference/sklearn-multiclass-linear-svc.joblib')
    model_bytes_io = BytesIO()
    model_file.download_to_file(model_bytes_io)
    model_bytes_io.seek(0)

    model = load(model_bytes_io)

    return model

def get_preference_ada_boost_model():
    bucket = storage.bucket('danbooru-ml-classifier')
    model_file = bucket.blob('preference/sklearn-multiclass-ada-boost.joblib')
    model_bytes_io = BytesIO()
    model_file.download_to_file(model_bytes_io)
    model_bytes_io.seek(0)

    model = load(model_bytes_io)

    return model

def get_preference_torch_network_model():
    bucket = storage.bucket('danbooru-ml-classifier')
    model_file = bucket.blob('preference/torch-multiclass-onehot-shallow-network-multilayer')
    model_bytes_io = BytesIO()
    model_file.download_to_file(model_bytes_io)
    model_bytes_io.seek(0)

    model = get_torch_network()
    model.load_state_dict(torch.load(model_bytes_io, map_location=torch.device('cpu')))

    return model

def get_deepdanbooru_model():
    bucket = storage.bucket('danbooru-ml-classifier')
    model_file = bucket.blob('deepdanbooru/0.1/resnet50-13306192.pth')
    model_bytes_io = BytesIO()
    model_file.download_to_file(model_bytes_io)
    model_bytes_io.seek(0)

    model = _resnet(models.resnet50, 6000)
    model.load_state_dict(torch.load(model_bytes_io, map_location=torch.device('cpu')))

    model.eval()

    return model

def get_top_tag_probs(tag_probs: torch.Tensor, threshold = 0.05):
    global class_names

    if class_names is None:
        print('Loading class names...')
        with urlopen("https://github.com/RF5/danbooru-pretrained/raw/master/config/class_names_6000.json") as url:
            class_names = json.loads(url.read().decode())
        print('Done loading class names')
    else:
        print('Class names already loaded')

    tmp = tag_probs[tag_probs > threshold]
    inds = tag_probs.argsort(descending=True)
    tag_dict = {}
    for i in inds[0:len(tmp)]:
        tag_dict[class_names[i]] = tag_probs[i].numpy()[()].item()

    return tag_dict

def inference_list_to_dict(inference_list: list):
    return dict(zip(
        ['not_bookmarked', 'bookmarked_public', 'bookmarked_private'],
        inference_list
    ))

def infer_image_preference(image_id: str, image_path: str):
    global preference_linear_svc_model, preference_ada_boost_model, preference_torch_network_model, deepdanbooru_model

    print(f'Infering image: {image_id} (mem = {get_rss()})')

    if deepdanbooru_model is None:
        deepdanbooru_model = get_deepdanbooru_model()
        print(f'DeepDanbooru model loaded (mem = {get_rss()})')
        print(deepdanbooru_model)

    if preference_linear_svc_model is None:
        preference_linear_svc_model = get_preference_linear_svc_model()
        print(f'Preference LinearSVC model loaded (mem = {get_rss()})')
        print(preference_linear_svc_model)

    if preference_ada_boost_model is None:
        preference_ada_boost_model = get_preference_ada_boost_model()
        print(f'Preference AdaBoost model loaded (mem = {get_rss()})')
        print(preference_ada_boost_model)

    if preference_torch_network_model is None:
        preference_torch_network_model = get_preference_torch_network_model()
        print(f'Preference Torch Network model loaded (mem = {get_rss()})')
        print(preference_torch_network_model)

    with open(image_path, 'rb') as image_file:
        try:
            image = Image.open(image_file)
        except UnidentifiedImageError as e:
            print(f'Error opening image: {e}')
            return

        print(f'Image opened: {image_id}')
        tags = get_raw_tags(deepdanbooru_model, image)

    os.remove(image_path)

    top_tag_probs = get_top_tag_probs(tags)
    print(f'Top tag probs inferred: {image_id}')

    linear_svc_preference = preference_linear_svc_model.decision_function(tags.numpy().reshape(1, -1))
    print(f'LinearSVC preference inferred: {image_id}')
    ada_boost_preference = preference_ada_boost_model.decision_function(tags.numpy().reshape(1, -1))
    print(f'AdaBoost preference inferred: {image_id}')
    torch_network_preference = preference_torch_network_model(tags)
    print(f'Torch Network preference inferred: {image_id}')

    return {
        'top_tag_probs': top_tag_probs,
        'inferences': {
            'sklearn_multiclass_linear_svc': inference_list_to_dict(linear_svc_preference.tolist()[0]),
            'sklearn_multiclass_ada_boost': inference_list_to_dict(ada_boost_preference.tolist()[0]),
            'torch_multiclass_onehot_shallow_network_multilayer': inference_list_to_dict(torch_network_preference.tolist()),
        },
    }

@firestore.transactional
def update_status_processing(transaction: firestore.Transaction):
    db = firestore.client()
    pending_images_iter = db.collection('images').where(filter=FieldFilter('status', '==', 'pending')).stream(transaction=transaction)
    pending_images = list(pending_images_iter)
    for image in pending_images:
        transaction.update(image.reference, {
            'status': 'processing',
        })
    return pending_images

@firestore_fn.on_document_created(
    memory=options.MemoryOption.GB_2,
    cpu=1,
    timeout_sec=540,
    document='images/{image_id}',
)
def onImageCreated(event: firestore_fn.Event[firestore_fn.DocumentSnapshot]):
    db = firestore.client()

    pending_count_result = db.collection('images').where(filter=FieldFilter('status', '==', 'pending')).count().get()
    pending_count = pending_count_result[0][0].value
    print(f'Pending count: {pending_count}')
    if pending_count < 100:
        return

    transaction = db.transaction()
    pending_images = update_status_processing(transaction)
    print(f'Got pending images: {len(pending_images)}')

    processing_images_iter = db.collection('images').where(filter=FieldFilter('status', '==', 'processing')).stream()
    processing_image_ids = set(processing_image.to_dict()['key'] for processing_image in processing_images_iter)
    print(f'Processing images: {len(processing_image_ids)}')

    pending_image_paths = download_images([image.to_dict()['key'] for image in pending_images])

    for image, image_path in zip(pending_images, pending_image_paths):
        if image_path is None:
            print(f'Error downloading image: {image_id}')
            image.reference.update({
                'status': 'error',
            })
            continue

        image_data = image.to_dict()
        image_id = image_data['key']
        print(f'Image created: {image_id}')

        inferences = infer_image_preference(image_id, image_path)
        print(f'Inferred: {image_id}')

        if inferences is None:
            print(f'Error inferring image: {image_id}')
            image.reference.update({
                'status': 'error',
            })
            continue

        image.reference.update({
            'status': 'inferred',
            'topTagProbs': inferences['top_tag_probs'],
            'inferences': inferences['inferences'],
        })

    new_processing_images_iter = db.collection('images').where(filter=FieldFilter('status', '==', 'processing')).stream()
    new_processing_images = list(new_processing_images_iter)
    print(f'New processing images: {len(new_processing_images)}')

    new_processing_image_paths = download_images([image.to_dict()['key'] for image in new_processing_images])

    for image, image_path in zip(new_processing_images, new_processing_image_paths):
        if image_path is None:
            print(f'Error downloading image: {image_id}')
            image.reference.update({
                'status': 'error',
            })
            continue

        image_data = image.to_dict()
        image_id = image_data['key']
        if image_id not in processing_image_ids:
            continue
        print(f'Image still processing: {image_id}')

        inferences = infer_image_preference(image_id, image_path)
        print(f'Inferred: {image_id}')

        if inferences is None:
            print(f'Error inferring image: {image_id}')
            image.reference.update({
                'status': 'error',
            })
            continue

        image.reference.update({
            'status': 'inferred',
            'topTagProbs': inferences['top_tag_probs'],
            'inferences': inferences['inferences'],
        })