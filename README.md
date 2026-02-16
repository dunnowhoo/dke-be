# propenheimer-dke-be

Custom Odoo 17 module untuk DKE Smart Sales Platform. Menangani manajemen penjualan, marketing campaign, chat terpadu, dan integrasi marketplace.

## Tech Stack

- **Backend**: Odoo 17 (Python 3.10+)
- **Database**: PostgreSQL 15
- **Container**: Docker & Docker Compose
- **Frontend**: Next.js (repo terpisah di `propenheimer-dke-fe`)

## Cara Menjalankan (Docker)

Pastikan Docker dan Docker Compose sudah terinstall.

```bash
# clone dan masuk ke folder
git clone <repository-url>
cd propenheimer-dke-be

# copy env
cp .env.example .env

# jalankan container
docker-compose up -d
# tunggu sekitar 30 detik sampai container siap

# buat database dan install module
docker exec -t dke-odoo odoo --db_host=db --db_user=odoo --db_password=odoo \
  -d dke_crm -i dke_crm --stop-after-init

# restart odoo
docker-compose restart odoo

# buka http://localhost:8069
# login pakai email dan password yang terbuat saat init
```

Kalau mau buat database lewat web UI (`http://localhost:8069/web/database/manager`), Master Password-nya `admin`, isi Database Name `dke_crm`. Setelah itu install module dari menu Apps, cari "DKE Smart Sales".

Untuk stop: `docker-compose down`. Kalau mau reset total (hapus data): `docker-compose down -v`.

### Environment Variables

Copy `.env.example` ke `.env`. Default-nya sudah siap pakai:

| Variable | Default | Keterangan |
|---|---|---|
| `POSTGRES_DB` | `odoo` | Nama database PostgreSQL |
| `POSTGRES_USER` | `odoo` | User PostgreSQL |
| `POSTGRES_PASSWORD` | `odoo` | Password PostgreSQL |
| `DB_PORT` | `5434` | Port PostgreSQL di host (sengaja bukan 5432 biar ga bentrok) |
| `ODOO_PORT` | `8069` | Port Odoo di host |

`odoo.conf` di-commit ke git tapi isinya cuma konfigurasi server, tidak ada secret. Kredensial database di-inject lewat Docker env dari `.env`.

### Instalasi Manual (tanpa Docker)

```bash
git clone <repository-url>
cd propenheimer-dke-be

# siapkan PostgreSQL
sudo -u postgres createuser -s odoo
sudo -u postgres createdb odoo --owner=odoo

# clone Odoo 17
git clone https://github.com/odoo/odoo.git --branch 17.0 --depth 1 ~/odoo-17
cd ~/odoo-17
pip install -r requirements.txt

# jalankan, addons-path harus ke PARENT folder dari propenheimer-dke-be
python3 odoo-bin \
    --addons-path=addons,/path/to/propenheimer-dke-be/.. \
    --db_host=localhost --db_user=odoo --db_password=odoo \
    -d dke_crm -i dke_crm
```

## Struktur Direktori

```
propenheimer-dke-be/
├── __init__.py
├── __manifest__.py          # manifest module Odoo
├── docker-compose.yml
├── odoo.conf
├── .env.example
├── .gitignore
├── models/                  # business models (ORM)
├── controllers/             # REST API endpoints
├── views/                   # XML views (tree, form, search)
├── security/                # access rights & security groups
├── data/                    # cron jobs & initial data
├── tests/                   # unit tests
├── wizard/
├── report/
├── static/
├── i18n/
└── .github/
    └── pull_request_template.md
```

## EPIC Overview

| EPIC | Deskripsi | Model |
|---|---|---|
| 01 | Chat Terpadu (Marketplace + WhatsApp) | chat.room, chat.message, marketplace.integration |
| 02 | Follow-Up Pembelian | scheduled.message |
| 03 | Promo Terpersonalisasi | marketing.campaign, customer.segment |
| 04 | Manajemen Transaksi Marketplace | sale.transaction |
| 05 | Pembelian & Invoicing | built-in Odoo (sale, account) |
| 06 | Ticketing Chat | support.ticket |
| 07 | Monitoring Chat CC | chat.monitoring |
| 08 | Monitoring Ticketing | ticket.monitoring |
| 09 | Dashboard EIS Sales Manager | views & reports |
| 10 | Manajemen Pemesanan Platform | sale order extension |

## User Roles

| Role | Security Group | Akses |
|---|---|---|
| Customer Care | `group_customer_care` | Chat, buat tiket, lihat monitoring sendiri |
| Sales Staff | `group_sales_staff` | Campaign, follow-up, transaksi (read) |
| Sales Manager | `group_sales_manager` | Full akses, dashboard, approve campaign |
| Expert Staff | `group_expert_staff` | Resolve tiket, monitoring tiket |

## Branching

```
main ← staging ← development ← feat/PBI-*
```

Setiap orang kerja di branch `feat/PBI-*` masing-masing, lalu buat PR ke `development`. Detail lengkap ada di [CONTRIBUTING.md](./CONTRIBUTING.md).
