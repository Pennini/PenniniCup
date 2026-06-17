import io
import os

from django import forms
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import InMemoryUploadedFile, UploadedFile
from PIL import Image, ImageOps, UnidentifiedImageError

from src.accounts.models import UserProfile

# Avatar exibido em 80x80, mas mantemos 1024px para boa definição em telas
# retina e em qualquer ampliação futura.
_MAX_IMAGE_DIMENSION = 1024
_JPEG_QUALITY = 90
# Limite anti decompression-bomb: ~50 megapixels cobre qualquer foto de celular.
# Acima disso o Pillow levanta DecompressionBombError em vez de alocar memória.
_MAX_IMAGE_PIXELS = 50_000_000


def _process_profile_image(uploaded):
    """Normaliza qualquer imagem (HEIC, PNG, JPG, WebP...) para um JPEG padronizado.

    Corrige orientação EXIF, redimensiona e reencoda para JPEG. Resolve tamanho
    das fotos de celular (3-8MB) e o formato HEIC do iPhone. O reencode descarta
    o container original (sanitiza) e o opener HEIF é registrado em apps.ready().
    """
    previous_limit = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = _MAX_IMAGE_PIXELS
    try:
        image = Image.open(uploaded)
        image = ImageOps.exif_transpose(image)
        image = image.convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValidationError("Não foi possível ler a imagem. Tente outra foto.") from exc
    finally:
        Image.MAX_IMAGE_PIXELS = previous_limit

    image.thumbnail((_MAX_IMAGE_DIMENSION, _MAX_IMAGE_DIMENSION))

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=_JPEG_QUALITY, optimize=True)
    buffer.seek(0)

    base_name = os.path.splitext(os.path.basename(uploaded.name or "avatar"))[0]
    return InMemoryUploadedFile(
        buffer,
        field_name="profile_image",
        name=f"{base_name}.jpg",
        content_type="image/jpeg",
        size=buffer.getbuffer().nbytes,
        charset=None,
    )


class ProfilePreferencesForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ["profile_image", "favorite_team", "world_cup_team"]

    def clean_profile_image(self):
        image = self.cleaned_data.get("profile_image")
        # Só processa uploads novos; FieldFile existente (sem mudança) passa direto.
        if isinstance(image, UploadedFile):
            return _process_profile_image(image)
        return image
