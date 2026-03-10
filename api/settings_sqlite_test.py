from .settings import *  # noqa: F401,F403

# Use sqlite for local test execution when Postgres is unavailable.
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'test_db.sqlite3',
    }
}

