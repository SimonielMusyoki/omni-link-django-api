from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient, APITestCase
from rest_framework import status
from django.utils import timezone
import secrets
from django.db import connection
from django.test.utils import CaptureQueriesContext

from products.models import Warehouse
from invitations.models import Invitation

User = get_user_model()


class InvitationModelTest(TestCase):
    """Tests for Invitation model"""

    def setUp(self):
        self.user = User.objects.create_user(
            email='inviter@test.com',
            password='testpass123'
        )
        self.warehouse = Warehouse.objects.create(
            name='Test Warehouse',
            location='NYC',
            address='123 Main St',
            capacity=1000,
            manager=self.user,
        )

    def test_create_invitation(self):
        invitation = Invitation.objects.create(
            email='invited@test.com',
            warehouse=self.warehouse,
            invited_by=self.user,
            token=secrets.token_urlsafe(32),
            expires_at=timezone.now() + timezone.timedelta(days=7),
        )
        self.assertEqual(invitation.email, 'invited@test.com')
        self.assertEqual(invitation.status, Invitation.PENDING)


class InvitationAPITest(APITestCase):
    """Tests for Invitation API endpoints"""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email='inviter@test.com',
            password='testpass123'
        )
        self.invited_user = User.objects.create_user(
            email='invited@test.com',
            password='testpass123'
        )
        self.client.force_authenticate(user=self.user)
        self.warehouse = Warehouse.objects.create(
            name='Test Warehouse',
            location='NYC',
            address='123 Main St',
            capacity=1000,
            manager=self.user,
        )

    def _assert_max_queries_for_get(self, url: str, max_queries: int):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertLessEqual(
            len(queries),
            max_queries,
            msg=f"Expected <= {max_queries} queries for {url}, got {len(queries)}",
        )
        return response

    def _query_count_for_get(self, url: str) -> int:
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return len(queries)

    def _seed_invitation(self, suffix: str):
        return Invitation.objects.create(
            email=f'invite-{suffix}@test.com',
            warehouse=self.warehouse,
            invited_by=self.user,
            invited_user=self.invited_user,
            token=secrets.token_urlsafe(32),
            expires_at=timezone.now() + timezone.timedelta(days=7),
        )

    def test_create_invitation(self):
        response = self.client.post(
            '/api/invitations/',
            {'email': 'newinvite@test.com', 'warehouse': self.warehouse.id},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['status'], Invitation.PENDING)

    def test_accept_invitation(self):
        invitation = Invitation.objects.create(
            email='invited@test.com',
            warehouse=self.warehouse,
            invited_by=self.user,
            invited_user=self.invited_user,
            token=secrets.token_urlsafe(32),
            expires_at=timezone.now() + timezone.timedelta(days=7),
        )

        self.client.force_authenticate(user=self.invited_user)
        response = self.client.post(f'/api/invitations/{invitation.id}/accept/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], Invitation.ACCEPTED)

    def test_reject_invitation(self):
        invitation = Invitation.objects.create(
            email='invited@test.com',
            warehouse=self.warehouse,
            invited_by=self.user,
            invited_user=self.invited_user,
            token=secrets.token_urlsafe(32),
            expires_at=timezone.now() + timezone.timedelta(days=7),
        )

        self.client.force_authenticate(user=self.invited_user)
        response = self.client.post(f'/api/invitations/{invitation.id}/reject/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], Invitation.REJECTED)

    def test_accept_by_token(self):
        token = secrets.token_urlsafe(32)
        Invitation.objects.create(
            email='invited@test.com',
            warehouse=self.warehouse,
            invited_by=self.user,
            token=token,
            expires_at=timezone.now() + timezone.timedelta(days=7),
        )

        self.client.force_authenticate(user=self.invited_user)
        response = self.client.post(
            '/api/invitations/accept_by_token/',
            {'token': token},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], Invitation.ACCEPTED)

    def test_expired_invitation(self):
        invitation = Invitation.objects.create(
            email='invited@test.com',
            warehouse=self.warehouse,
            invited_by=self.user,
            invited_user=self.invited_user,
            token=secrets.token_urlsafe(32),
            expires_at=timezone.now() - timezone.timedelta(days=1),
        )

        self.client.force_authenticate(user=self.invited_user)
        response = self.client.post(f'/api/invitations/{invitation.id}/accept/')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_list_invitations_query_count(self):
        Invitation.objects.create(
            email='invite1@test.com',
            warehouse=self.warehouse,
            invited_by=self.user,
            invited_user=self.invited_user,
            token=secrets.token_urlsafe(32),
            expires_at=timezone.now() + timezone.timedelta(days=7),
        )
        Invitation.objects.create(
            email='invite2@test.com',
            warehouse=self.warehouse,
            invited_by=self.user,
            invited_user=self.invited_user,
            token=secrets.token_urlsafe(32),
            expires_at=timezone.now() + timezone.timedelta(days=7),
        )

        response = self._assert_max_queries_for_get('/api/invitations/', max_queries=5)
        self.assertEqual(response.data['count'], 2)

    def test_list_invitations_query_count_growth_is_bounded(self):
        Invitation.objects.all().delete()
        self._seed_invitation('base')
        baseline_queries = self._query_count_for_get('/api/invitations/')

        Invitation.objects.all().delete()
        for idx in range(20):
            self._seed_invitation(f'n-{idx}')
        many_queries = self._query_count_for_get('/api/invitations/')

        self.assertLessEqual(
            many_queries,
            baseline_queries + 2,
            msg=(
                f'N+1 regression detected for invitations list: '
                f'baseline={baseline_queries}, many={many_queries}'
            ),
        )

