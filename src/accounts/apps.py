from django.apps import AppConfig


class AccountsConfig(AppConfig):
    name = "src.accounts"

    def ready(self):
        # Habilita Pillow a decodificar HEIC/HEIF/AVIF (fotos do iPhone).
        # A foto é reencodada para JPEG no upload (forms), o que descarta o
        # container HEIF original e limita a exposição ao decoder nativo.
        from pillow_heif import register_heif_opener

        register_heif_opener()
