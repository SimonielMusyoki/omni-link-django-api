# Omni Link Platform

Omni Link is an inventory, commerce, and operations platform built with a Django REST API and a Vite/React dashboard.

It supports multi-market operations (for example Kenya and Nigeria), product and warehouse inventory management, Shopify synchronization, integrations (QuickBooks, Odoo, Shopify), order and shipment workflows, and internal request/approval processes.

## What is in this repository

- Backend API: Django + Django REST Framework (`/api`, app folders)
- Frontend dashboard: Vite + React (`/uncover-omni-link`)

## Core capabilities

### Authentication and user management

- Email/password registration and login
- Google sign-in support
- JWT access/refresh token authentication
- Role-based access levels (`ADMIN`, `MANAGER`, `USER`, `GUEST`)
- Profile management and password change APIs
- Admin-only user listing and management endpoints

### Products, categories, and inventory

- Product catalog with categories
- Product types:
  - Simple products
  - Bundle/kit products made of other products
- Product metadata including SKU, image URL, and stock-related fields
- Warehouse management (create, edit, delete)
- Inventory by warehouse
- Mass inventory editing UX in dashboard
- Inventory transfer between warehouses (including multi-product transfers)
- Markets managed from backend and selectable in app flows

### Product requests and approvals

- Product request creation with multiple line items
- Requester defaults to logged-in user
- Assigned approver (must be a system user)
- Approval/rejection workflows
- Ready-to-collect transition by warehouse manager
- Request timeline with persisted events (`ProductRequestEvent`)
- Notification flow hooks (approver, warehouse manager, requester)

### Integrations and synchronization

- Integrations app supports:
  - Shopify
  - Odoo
  - QuickBooks
- Per-market integration constraints and management
- Integration-specific credential handling
- Integration CRUD with validation and test-connection actions
- Shopify sync endpoints for:
  - Orders (date-range sync)
  - Products (including bundle-aware flow)
- Shopify webhook endpoints for:
  - `orders/create`
  - `orders/updated`
  - `products/create`
  - `products/updated`
- Webhook idempotency with `X-Shopify-Webhook-Id`

### Orders and analytics

- Shopify order ingestion and storage
- Market-aware order handling (market, currency, channel behavior)
- Rich order model support for payment, delivery, fulfillment, channel, tax, shipping, metadata
- Paginated order APIs
- Date-range and market filtering in dashboard
- Order detail views and status displays
- Analytics page with market and channel performance views

### Shipments

- Shipment records persisted in database
- Shipment creation and status workflow
- Shipment line items with multiple products
- Destination warehouse handling and stock update on receive

## API overview

### Base routes

- Health: `GET /api/health/`
- API docs (Swagger): `GET /api/docs/`
- OpenAPI schema: `GET /api/schema/`

### Authentication

- `POST /api/auth/register/`
- `POST /api/auth/login/`
- `POST /api/auth/logout/`
- `POST /api/auth/token/refresh/`
- `POST /api/auth/google/login/`
- `GET/PATCH /api/auth/profile/`
- `POST /api/auth/change-password/`
- `GET /api/auth/users/` (admin)
- `GET/PATCH/DELETE /api/auth/users/<id>/` (admin)

### Main resource routers (`/api/`)

- `markets`
- `categories`
- `warehouses`
- `products`
- `kit-items`
- `inventory`
- `transfers`
- `integrations`
- `orders`
- `shipments`
- `product-requests`
- `invitations`

### Shopify webhooks

- `POST /api/webhooks/shopify/orders/create/`
- `POST /api/webhooks/shopify/orders/updated/`
- `POST /api/webhooks/shopify/products/create/`
- `POST /api/webhooks/shopify/products/updated/`

## Local development

## Prerequisites

- Python 3.13+
- Node.js 20+
- PostgreSQL
- (Optional) Docker for local DB/testing convenience

### 1) Backend setup

```bash
cd /Users/Musyoki/Dev/Django/omni-link-api
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Create/update backend env file (`.env`) with at least:

```env
DB_NAME=omnilink_db
DB_USER=omnilink_user
DB_PASSWORD=omnilink_password
DB_HOST=127.0.0.1
DB_PORT=5432
SECRET_KEY=change-me
DEBUG=True
GOOGLE_OAUTH_CLIENT_ID=your-google-client-id.apps.googleusercontent.com
GOOGLE_OAUTH_CLIENT_SECRET=your-google-client-secret
SHOPIFY_WEBHOOK_SECRET=your-shopify-webhook-secret
```

Run migrations and start API:

```bash
python manage.py migrate
python manage.py runserver
```

### 2) Frontend setup

```bash
cd /Users/Musyoki/Dev/Django/omni-link-api/uncover-omni-link
npm ci
```

Create/update frontend env file (`uncover-omni-link/.env`):

```env
VITE_API_BASE_URL=http://127.0.0.1:8000/api
VITE_AUTH_STORAGE_KEY=omni_link_auth
VITE_GOOGLE_CLIENT_ID=your-google-client-id.apps.googleusercontent.com
```

Start frontend:

```bash
npm run dev
```

## Testing

Backend tests:

```bash
cd /Users/Musyoki/Dev/Django/omni-link-api
source venv/bin/activate
pytest
```

Frontend build check:

```bash
cd /Users/Musyoki/Dev/Django/omni-link-api/uncover-omni-link
npm run build
```

## Deployment notes (current direction)

- Production deployment is configured for non-Docker runtime on EC2:
  - Django app served with Gunicorn + Nginx
  - PostgreSQL on AWS RDS
  - Frontend hosted on S3 + CloudFront
- Docker files remain available for local development/testing.

See:

- `DEPLOYMENT_PIPELINES_README.md`
- `deploy/systemd/README.md`
- `.github/workflows/backend-ec2.yml`

## Project structure (high level)

```text
omni-link-api/
├── api/
├── authentication/
├── products/
├── orders/
├── shipments/
├── product_requests/
├── invitations/
├── integrations/
├── deploy/
│   ├── nginx/
│   └── systemd/
├── uncover-omni-link/
└── README.md
```

## Documentation index

For implementation details and historical notes, see the markdown docs in repository root, including:

- `DOCUMENTATION_INDEX.md`
- `API_DOCUMENTATION.md`
- `AUTHENTICATION_README.md`
- `ANALYTICS_IMPLEMENTATION.md`
- `PRODUCT_REQUESTS_IMPLEMENTATION.md`
- `SHIPMENTS_IMPLEMENTATION_COMPLETE.md`

## License

Proprietary - All rights reserved.
