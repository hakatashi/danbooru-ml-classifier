from firebase_admin import storage, initialize_app
from io import BytesIO
from PIL import Image, UnidentifiedImageError
from multiprocessing import current_process
from tempfile import NamedTemporaryFile

if current_process().name != 'MainProcess':
    print('Child process started')
    cnt = 0
    initialize_app()

def download_image(image_id: str):
    global cnt
    cnt += 1

    print(f'Start download_image: {image_id} (cnt = {cnt})')

    bucket = storage.bucket('danbooru-ml-classifier-images')
    image_file = bucket.blob(image_id)
    image_bytes_io = BytesIO()
    image_file.download_to_file(image_bytes_io)
    image_bytes_io.seek(0)

    print(f'Image downloaded: {image_id}')

    try:
        image = Image.open(image_bytes_io)
    except UnidentifiedImageError as e:
        print(f'Error opening image: {e}')
        return None

    print(f'Image opened: {image_id}')

    # Resize the image so that the shorter side is 360 pixels
    width, height = image.size
    if width < height:
        new_width = 360
        new_height = int(new_width * height / width)
    else:
        new_height = 360
        new_width = int(new_height * width / height)

    image = image.resize((new_width, new_height), Image.BILINEAR)

    print(f'Image resized: {image_id}')

    try:
        if image.mode != 'RGB':
            image = image.convert('RGB')
    except Exception as e:
        print('Image conversion failed')
        print(e)

    with NamedTemporaryFile(delete=False) as temp:
        image.save(temp, format='JPEG', quality=100, subsampling=0)
        output_path = temp.name

    print(f'Image saved: {image_id}')

    return output_path

def download_images(image_ids: list):
    from multiprocessing import Pool

    print('Start download_images')
    with Pool(processes=8, maxtasksperchild=1) as p:
        results = p.map(download_image, image_ids)
    print('End download_images')

    return results