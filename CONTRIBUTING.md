# Contributing to DKE Smart Sales Platform

Panduan kontribusi untuk anggota tim Propenheimer.

## Branching Strategy

Kami menggunakan **Git Flow** dengan 3 branch utama:

```
main          ← Production-ready, protected branch
staging       ← Pre-release testing & demo sprint review
development   ← Integration branch, semua feature merge ke sini dulu
```

### Feature Branches

Setiap anggota **wajib bekerja di branch sendiri**, bukan langsung di `development`.

**Naming convention:**
```
<type>/<PBI-number>-<short-description>

Contoh:
feat/PBI-1-chat-integration
feat/PBI-6-promo-campaign
fix/PBI-17-ticket-sla-bug
docs/PBI-readme-update
```

**Tipe branch:**
| Prefix | Kegunaan |
|--------|----------|
| `feat/` | Fitur baru |
| `fix/` | Bug fix |
| `docs/` | Dokumentasi |
| `refactor/` | Refactoring tanpa ubah behavior |
| `test/` | Menambah/memperbaiki test |

## Workflow

### 1. Mulai Fitur Baru

```bash
# Pastikan development up-to-date
git checkout development
git pull origin development

# Buat branch baru
git checkout -b feat/PBI-1-chat-integration
```

### 2. Commit Messages

Gunakan format **Conventional Commits**:

```
<type>(<scope>): <description>

Contoh:
feat(chat): add chat room model and views
fix(ticket): resolve SLA deadline calculation bug
docs(readme): update installation guide
refactor(models): extract marketplace sync logic
test(chat): add unit tests for chat room creation
```

### 3. Push & Pull Request

```bash
git push origin feat/PBI-1-chat-integration
```

Lalu buat **Pull Request** ke `development` di GitHub dengan:
- Judul yang jelas
- Deskripsi sesuai template PR
- Assign minimal 1 reviewer
- Link ke PBI/issue yang dikerjakan

### 4. Code Review & Merge

- Minimal **1 approval** sebelum merge
- Pastikan tidak ada conflict
- Gunakan **Squash and Merge** untuk commit history yang bersih
- Hapus branch setelah merge

### 5. Release ke Staging

```bash
# Merge development ke staging untuk demo
git checkout staging
git pull origin staging
git merge development
git push origin staging
```

### 6. Release ke Main (Production)

```bash
# Setelah sprint review & approval
git checkout main
git pull origin main
git merge staging
git tag -a v1.0.0 -m "Sprint 1 Release"
git push origin main --tags
```

## Code Style

- **Python**: Ikuti PEP 8
- **XML**: Indent 4 spasi
- **Nama model**: `dke.<domain>.<entity>` (contoh: `dke.chat.room`)
- **Nama file**: snake_case (contoh: `chat_room.py`)

## Checklist Sebelum PR

- [ ] Code sudah di-test manual
- [ ] Tidak ada syntax error (`python3 -m py_compile`)
- [ ] XML valid (`xmllint` atau Python `xml.etree`)
- [ ] Commit message sesuai convention
- [ ] `__manifest__.py` sudah di-update jika ada file baru
- [ ] `security/ir.model.access.csv` sudah di-update jika ada model baru
