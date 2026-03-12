from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from authentication.models import UserRole
from products.models import Warehouse, Product
from product_requests.models import ProductRequest, ProductRequestEvent

User = get_user_model()


class RequestModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='request@test.com', password='testpass123')
        self.approver = User.objects.create_user(email='approver@test.com', password='testpass123')
        self.warehouse = Warehouse.objects.create(
            name='Test Warehouse',
            location='NYC',
            address='123 Main St',
            capacity=1000,
            manager=self.user,
        )

    def test_create_request_defaults(self):
        req = ProductRequest.objects.create(
            reason='Need inventory transfer',
            requested_by=self.user,
            approver=self.approver,
            warehouse=self.warehouse,
        )
        self.assertEqual(req.status, ProductRequest.PENDING)
        self.assertIsNone(req.ready_at)


class RequestWorkflowAPITest(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.requester = User.objects.create_user(email='request@test.com', password='testpass123', role=UserRole.USER)
        self.approver = User.objects.create_user(email='approver@test.com', password='testpass123', role=UserRole.MANAGER)
        self.manager = User.objects.create_user(email='manager@test.com', password='testpass123', role=UserRole.MANAGER)
        self.other_user = User.objects.create_user(email='other@test.com', password='testpass123')
        self.warehouse = Warehouse.objects.create(
            name='Test Warehouse',
            location='NYC',
            address='123 Main St',
            capacity=1000,
            manager=self.manager,
        )
        self.product_a = Product.objects.create(
            name='Aloe Gel',
            sku='REQ-ALOE-001',
            price='10.00',
        )
        self.product_b = Product.objects.create(
            name='Vitamin C',
            sku='REQ-VITC-001',
            price='15.00',
        )

    def _create_request(self):
        return ProductRequest.objects.create(
            reason='Need stock',
            requested_by=self.requester,
            approver=self.approver,
            warehouse=self.warehouse,
        )

    @patch('product_requests.services.send_request_created_to_approver.delay')
    def test_create_request_queues_approver_email(self, mocked_delay):
        self.client.force_authenticate(user=self.requester)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                '/api/product-requests/',
                {
                    'reason': 'Need inventory transfer',
                    'approver': self.approver.id,
                    'warehouse': self.warehouse.id,
                    'items': [
                        {'product': self.product_a.id, 'quantity': 2},
                        {'product': self.product_b.id, 'quantity': 1},
                    ],
                },
                format='json',
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        mocked_delay.assert_called_once()
        self.assertEqual(len(response.data['items']), 2)
        event_types = list(
            ProductRequestEvent.objects.filter(request_id=response.data['id']).values_list('event_type', flat=True)
        )
        self.assertIn(ProductRequestEvent.REQUEST_CREATED, event_types)

    @patch('product_requests.services.send_request_created_to_approver.delay', side_effect=AttributeError("'NoneType' object has no attribute 'Redis'"))
    def test_create_request_succeeds_when_task_enqueue_fails(self, _mocked_delay):
        self.client.force_authenticate(user=self.requester)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                '/api/product-requests/',
                {
                    'reason': 'Need inventory transfer',
                    'approver': self.approver.id,
                    'warehouse': self.warehouse.id,
                    'items': [
                        {'product': self.product_a.id, 'quantity': 1},
                    ],
                },
                format='json',
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_request_requires_approver_and_warehouse(self):
        self.client.force_authenticate(user=self.requester)
        response = self.client.post('/api/product-requests/', {'reason': 'Need stock'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('approver', response.data)
        self.assertIn('warehouse', response.data)
        self.assertIn('items', response.data)

    def test_non_manager_user_sees_only_own_product_requests(self):
        own_req = self._create_request()
        ProductRequest.objects.create(
            reason='Other request',
            requested_by=self.other_user,
            approver=self.approver,
            warehouse=self.warehouse,
        )

        self.client.force_authenticate(user=self.requester)
        response = self.client.get('/api/product-requests/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['id'], own_req.id)

    def test_manager_can_see_all_product_requests(self):
        self._create_request()
        ProductRequest.objects.create(
            reason='Other request',
            requested_by=self.other_user,
            approver=self.approver,
            warehouse=self.warehouse,
        )

        self.client.force_authenticate(user=self.manager)
        response = self.client.get('/api/product-requests/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 2)

    @patch('product_requests.services.send_request_approved_to_manager.delay')
    def test_only_assigned_approver_can_approve(self, mocked_delay):
        req = self._create_request()

        self.client.force_authenticate(user=self.other_user)
        denied = self.client.post(f'/api/product-requests/{req.id}/approve/')
        self.assertEqual(denied.status_code, status.HTTP_404_NOT_FOUND)

        self.client.force_authenticate(user=self.approver)
        with self.captureOnCommitCallbacks(execute=True):
            ok = self.client.post(f'/api/product-requests/{req.id}/approve/')
        self.assertEqual(ok.status_code, status.HTTP_200_OK)
        self.assertEqual(ok.data['status'], ProductRequest.APPROVED)
        mocked_delay.assert_called_once_with(req.id)
        self.assertTrue(
            ProductRequestEvent.objects.filter(
                request=req,
                event_type=ProductRequestEvent.REQUEST_APPROVED,
            ).exists()
        )

    def test_only_assigned_approver_can_reject(self):
        req = self._create_request()

        self.client.force_authenticate(user=self.other_user)
        denied = self.client.post(
            f'/api/product-requests/{req.id}/reject/',
            {'reason': 'no'},
            format='json',
        )
        self.assertEqual(denied.status_code, status.HTTP_404_NOT_FOUND)

        self.client.force_authenticate(user=self.approver)
        ok = self.client.post(
            f'/api/product-requests/{req.id}/reject/',
            {'reason': 'Insufficient inventory'},
            format='json',
        )
        self.assertEqual(ok.status_code, status.HTTP_200_OK)
        self.assertEqual(ok.data['status'], ProductRequest.REJECTED)

    @patch('product_requests.services.send_request_ready_to_collect_to_requester.delay')
    def test_manager_marks_ready_to_collect_and_queues_requester_email(self, mocked_delay):
        req = self._create_request()
        req.status = ProductRequest.APPROVED
        req.save(update_fields=['status'])

        self.client.force_authenticate(user=self.other_user)
        denied = self.client.post(f'/api/product-requests/{req.id}/ready-to-collect/')
        self.assertEqual(denied.status_code, status.HTTP_404_NOT_FOUND)

        self.client.force_authenticate(user=self.manager)
        with self.captureOnCommitCallbacks(execute=True):
            ok = self.client.post(f'/api/product-requests/{req.id}/ready-to-collect/')
        self.assertEqual(ok.status_code, status.HTTP_200_OK)
        self.assertEqual(ok.data['status'], ProductRequest.READY_TO_COLLECT)
        mocked_delay.assert_called_once_with(req.id)
        self.assertTrue(
            ProductRequestEvent.objects.filter(
                request=req,
                event_type=ProductRequestEvent.REQUEST_READY_TO_COLLECT,
            ).exists()
        )

    def test_request_detail_includes_timeline_events(self):
        req = self._create_request()
        ProductRequestEvent.objects.create(
            request=req,
            event_type=ProductRequestEvent.REQUEST_CREATED,
            actor=self.requester,
            note='request created',
        )

        self.client.force_authenticate(user=self.requester)
        response = self.client.get(f'/api/product-requests/{req.id}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('timeline_events', response.data)
        self.assertEqual(len(response.data['timeline_events']), 1)

    def test_email_task_persists_timeline_event(self):
        req = self._create_request()
        from product_requests.tasks import send_request_created_to_approver

        send_request_created_to_approver(req.id)

        self.assertTrue(
            ProductRequestEvent.objects.filter(
                request=req,
                event_type=ProductRequestEvent.EMAIL_TO_APPROVER_SENT,
            ).exists()
        )

    def test_timeline_event_label_fields_in_api_response(self):
        """Each timeline event in the detail endpoint must carry label.{title,description,color,icon_name}."""
        req = self._create_request()
        for event_type in [
            ProductRequestEvent.REQUEST_CREATED,
            ProductRequestEvent.REQUEST_APPROVED,
            ProductRequestEvent.EMAIL_TO_APPROVER_SENT,
        ]:
            ProductRequestEvent.objects.create(
                request=req,
                event_type=event_type,
                actor=self.requester,
                note='',
            )

        self.client.force_authenticate(user=self.requester)
        response = self.client.get(f'/api/product-requests/{req.id}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        for event in response.data['timeline_events']:
            label = event.get('label')
            self.assertIsNotNone(label, f"event {event['event_type']} missing label")
            self.assertIn('title', label)
            self.assertIn('description', label)
            self.assertIn('color', label)
            self.assertIn('icon_name', label)
            self.assertTrue(label['title'], "label.title must not be empty")
            self.assertTrue(label['color'].startswith('#'), "label.color must be a hex color")

    def test_timeline_label_note_overrides_description(self):
        """When a note is set, label.description must equal the note (not the generic template)."""
        req = self._create_request()
        note_text = 'jane@example.com approved this request.'
        ProductRequestEvent.objects.create(
            request=req,
            event_type=ProductRequestEvent.REQUEST_APPROVED,
            actor=self.approver,
            note=note_text,
        )

        self.client.force_authenticate(user=self.requester)
        response = self.client.get(f'/api/product-requests/{req.id}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        approved_events = [
            e for e in response.data['timeline_events']
            if e['event_type'] == ProductRequestEvent.REQUEST_APPROVED
        ]
        self.assertEqual(len(approved_events), 1)
        self.assertEqual(approved_events[0]['label']['description'], note_text)

