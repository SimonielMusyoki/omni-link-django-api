import os
import django
from django.conf import settings
from django.test.utils import get_runner

if __name__ == "__main__":
    os.environ['DJANGO_SETTINGS_MODULE'] = 'api.settings'
    django.setup()
    TestRunner = get_runner(settings)
    test_runner = TestRunner()

    # Run tests
    failures = test_runner.run_tests([
        'authentication.tests',
        'products.tests',
        'orders.tests',
        'shipments.tests',
        'requests.tests',
        'invitations.tests',
    ])

    exit(bool(failures))

