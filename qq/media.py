"""OneBot image segments. Callers supply validated controller-owned bytes."""
import base64


def image_segments(data, caption=''):
    if not data or len(data)>5*1024*1024:
        raise ValueError('qq_image_size_limit')
    text=str(caption)[:500]
    return ([{'type':'text','data':{'text':text}}] if text else []) + [
        {'type':'image','data':{'file':'base64://'+base64.b64encode(data).decode()}}]
