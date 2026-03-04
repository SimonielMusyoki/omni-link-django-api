import pytest
from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient, APITestCase
from rest_framework import status
from django.utils import timezone
import secrets

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
            owner=self.user
        )

    def test_create_invitation(self):
        """Test creating an invitation"""
        invitation = Invitation.objects.create(
            email='invited@test.com',
            warehouse=self.warehouse,
            invited_by=self.user,
            token=secrets.token_urlsafe(32),
            expires_at=timezone.now() + timezone.timedelta(days=7)
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
            owner=self.user
        )

    def test_create_invitation(self):
        """Test creating invitation via API"""
        data = {
            'email': 'newinvite@test.com',
            'warehouse': self.warehouse.id
        }
        response = self.client.post('/api/invitations/', data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['email'], 'newinvite@test.com')
        self.assertEqual(response.data['status'], 'PENDING')

    def test_accept_invitation(self):
        """Test accepting an invitation"""
        invitation = Invitation.objects.create(
            email='invited@test.com',
            warehouse=self.warehouse,
            invited_by=self.user,
            token=secrets.token_urlsafe(32),
            expires_at=timezone.now() + timezone.timedelta(days=7)
        )

        self.client.force_authenticate(user=self.invited_user)
        response = self.client.post(f'/api/invitations/{invitation.id}/accept/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'ACCEPTED')
        self.assertIsNotNone(response.data['accepted_at'])

    def test_reject_invitation(self):
        """Test rejecting an invitation"""
        invitation = Invitation.objects.create(
            email='invited@test.com',
            warehouse=self.warehouse,
            invited_by=self.user,
            token=secrets.token_urlsafe(32),
            expires_at=timezone.now() + timezone.timedelta(days=7)
        )

        self.client.force_authenticate(user=self.invited_user)
        response = self.client.post(f'/api/invitations/{invitation.id}/reject/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'REJECTED')

    def test_accept_by_token(self):
        """Test accepting invitation by token"""
        token = secrets.token_urlsafe(32)
        invitation = Invitation.objects.create(
            email='invited@test.com',
            warehouse=self.warehouse,
            invited_by=self.user,
            token=token,
            expires_at=timezone.now() + timezone.timedelta(days=7)
        )

        data = {'token': token}
        self.client.force_authenticate(user=self.invited_user)
        response = self.client.post('/api/invitations/accept_by_token/', data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'ACCEPTED')

    def test_expired_invitation(self):
        """Test that expired invitations cannot be accepted"""
        invitation = Invitation.objects.create(
            email='invited@test.com',
            warehouse=self.warehouse,
            invited_by=self.user,
            token=secrets.token_urlsafe(32),
            expires_at=timezone.now() - timezone.timedelta(days=1)
        )

        self.client.force_authenticate(user=self.invited_user)
        response = self.client.post(f'/api/invitations/{invitation.id}/accept/')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

