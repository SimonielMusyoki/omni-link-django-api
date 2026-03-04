import pytest
from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient, APITestCase
from rest_framework import status
from django.utils import timezone

from products.models import Warehouse, Product
from requests.models import Request
from decimal import Decimal

User = get_user_model()


class RequestModelTest(TestCase):
    """Tests for Request model"""

    def setUp(self):
        self.user = User.objects.create_user(
            email='request@test.com',
            password='testpass123'
        )
        self.warehouse = Warehouse.objects.create(
            name='Test Warehouse',
            location='NYC',
            address='123 Main St',
            capacity=1000,
            owner=self.user
        )

    def test_create_request(self):
        """Test creating a request"""
        request_obj = Request.objects.create(
            title='Test Request',
            description='This is a test request',
            type=Request.TRANSFER,
            requested_by=self.user,
            related_warehouse=self.warehouse
        )
        self.assertEqual(request_obj.title, 'Test Request')
        self.assertEqual(request_obj.status, Request.PENDING)


class RequestAPITest(APITestCase):
    """Tests for Request API endpoints"""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email='request@test.com',
            password='testpass123'
        )
        self.user2 = User.objects.create_user(
            email='approver@test.com',
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

    def test_create_request(self):
        """Test creating request via API"""
        data = {
            'title': 'Test Request',
            'description': 'This is a test request',
            'type': 'TRANSFER',
            'related_warehouse': self.warehouse.id
        }
        response = self.client.post('/api/requests/', data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['title'], 'Test Request')

    def test_list_requests(self):
        """Test listing requests"""
        Request.objects.create(
            title='Request 1',
            description='Description 1',
            type=Request.TRANSFER,
            requested_by=self.user
        )
        Request.objects.create(
            title='Request 2',
            description='Description 2',
            type=Request.REFUND,
            requested_by=self.user
        )

        response = self.client.get('/api/requests/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 2)

    def test_approve_request(self):
        """Test approving a request"""
        request_obj = Request.objects.create(
            title='Test Request',
            description='This is a test request',
            type=Request.TRANSFER,
            requested_by=self.user
        )

        self.client.force_authenticate(user=self.user2)
        response = self.client.post(f'/api/requests/{request_obj.id}/approve/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'APPROVED')

    def test_reject_request(self):
        """Test rejecting a request"""
        request_obj = Request.objects.create(
            title='Test Request',
            description='This is a test request',
            type=Request.TRANSFER,
            requested_by=self.user
        )

        data = {'reason': 'Insufficient inventory'}
        self.client.force_authenticate(user=self.user2)
        response = self.client.post(f'/api/requests/{request_obj.id}/reject/', data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'REJECTED')
        self.assertEqual(response.data['rejection_reason'], 'Insufficient inventory')

