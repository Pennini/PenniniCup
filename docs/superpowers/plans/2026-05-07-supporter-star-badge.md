# Supporter Star Badge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a gold star icon to the profile page for users manually marked as supporters (donors) by an admin.

**Architecture:** Add `is_supporter` boolean field to `UserProfile`, expose it in Django admin with inline list editing, and render a conditional Lucide star icon in the profile template.

**Tech Stack:** Django 6, Python 3.12, TailwindCSS, Lucide icons (already loaded)

______________________________________________________________________

## Files

| File                                                       | Change                                                               |
| ---------------------------------------------------------- | -------------------------------------------------------------------- |
| `src/accounts/models.py`                                   | Add `is_supporter` field to `UserProfile`                            |
| `src/accounts/migrations/0006_userprofile_is_supporter.py` | New migration (auto-generated)                                       |
| `src/accounts/admin.py`                                    | Add `is_supporter` to `list_display`, `list_filter`, `list_editable` |
| `src/penninicup/templates/penninicup/profile.html`         | Add conditional star icon next to username                           |
| `src/accounts/tests.py`                                    | Add two view tests for star shown/hidden                             |

______________________________________________________________________

### Task 1: Add `is_supporter` field to `UserProfile`

**Files:**

- Modify: `src/accounts/models.py`

- [ ] **Step 1: Add field to model**

In `src/accounts/models.py`, add `is_supporter` to the `UserProfile` class after `world_cup_team`:

```python
is_supporter = models.BooleanField(default=False)
```

The full `UserProfile` field list after the change (fields only, no methods):

```python
user = models.OneToOneField("accounts.CustomUser", on_delete=models.CASCADE, related_name="profile")
email_verified = models.BooleanField(default=False)
verification_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
token_created_at = models.DateTimeField(auto_now_add=True)
profile_image = models.ImageField(upload_to="profiles/", blank=True, null=True, validators=[_validate_profile_image])
favorite_team = models.CharField(max_length=120, blank=True)
world_cup_team = models.ForeignKey(
    "football.Team",
    on_delete=models.SET_NULL,
    null=True,
    blank=True,
    related_name="supporter_profiles",
)
is_supporter = models.BooleanField(default=False)
```

- [ ] **Step 2: Generate migration**

```bash
poetry run python -m src.manage makemigrations accounts --name userprofile_is_supporter
```

Expected output: `Migrations for 'accounts': src/accounts/migrations/0006_userprofile_is_supporter.py`

- [ ] **Step 3: Apply migration**

```bash
poetry run python -m src.manage migrate
```

Expected: migration applies cleanly with no errors.

- [ ] **Step 4: Commit**

```bash
git add src/accounts/models.py src/accounts/migrations/0006_userprofile_is_supporter.py
git commit -m "feat(accounts): add is_supporter field to UserProfile"
```

______________________________________________________________________

### Task 2: Update admin

**Files:**

- Modify: `src/accounts/admin.py`

- [ ] **Step 1: Update `UserProfileAdmin`**

Replace the `UserProfileAdmin` class in `src/accounts/admin.py` with:

```python
@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ["user", "is_supporter", "email_verified", "favorite_team", "world_cup_team", "token_created_at"]
    list_filter = ["is_supporter", "email_verified", "favorite_team", "world_cup_team"]
    list_editable = ["is_supporter"]
    search_fields = ["user__username", "user__email", "favorite_team", "world_cup_team__name"]
    readonly_fields = ["verification_token", "token_created_at"]
```

- [ ] **Step 2: Verify admin works**

```bash
poetry run python -m src.manage check
```

Expected: `System check identified no issues (0 silenced).`

- [ ] **Step 3: Commit**

```bash
git add src/accounts/admin.py
git commit -m "feat(accounts): expose is_supporter in UserProfile admin with list_editable"
```

______________________________________________________________________

### Task 3: Add star icon to profile template

**Files:**

- Modify: `src/penninicup/templates/penninicup/profile.html`

- [ ] **Step 1: Update the username heading**

On line 47, replace:

```html
<h2 class="text-2xl font-semibold break-all">{{ profile_user.username }}</h2>
```

With:

```html
<h2 class="text-2xl font-semibold break-all flex items-center gap-2">
    {{ profile_user.username }}
    {% if profile_obj.is_supporter %}
    <i data-lucide="star" class="inline h-5 w-5 text-yellow-400 fill-yellow-400 shrink-0"></i>
    {% endif %}
</h2>
```

- [ ] **Step 2: Commit**

```bash
git add src/penninicup/templates/penninicup/profile.html
git commit -m "feat(penninicup): show gold star on profile page for supporters"
```

______________________________________________________________________

### Task 4: Add tests

**Files:**

- Modify: `src/accounts/tests.py`

- [ ] **Step 1: Write failing tests**

Add a new test class at the end of `src/accounts/tests.py`:

```python
class SupporterStarBadgeTest(TestCase):
    """Testes para exibição da estrela dourada de apoiador na página de perfil"""

    def setUp(self):
        self.user = User.objects.create_user(
            username="staruser",
            email="star@example.com",
            password="testpass123",
            is_active=True,
        )
        self.profile = UserProfile.objects.get_or_create(user=self.user)[0]
        self.url = reverse("penninicup:profile-user", kwargs={"username": self.user.username})

    def test_supporter_star_shown(self):
        """Usuário apoiador exibe ícone de estrela no perfil"""
        self.profile.is_supporter = True
        self.profile.save()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-lucide="star"')

    def test_supporter_star_hidden(self):
        """Usuário não apoiador não exibe ícone de estrela no perfil"""
        self.profile.is_supporter = False
        self.profile.save()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-lucide="star"')
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
DJANGO_SETTINGS_PROFILE=test poetry run python -m src.manage test src.accounts.tests.SupporterStarBadgeTest --verbosity=2
```

Expected: 2 tests fail (field does not exist yet on this step — but if running after Task 1-3, they should pass). If running TDD-style before Task 1, `test_supporter_star_shown` fails with `AttributeError: 'UserProfile' object has no attribute 'is_supporter'`.

- [ ] **Step 3: Run full accounts test suite**

```bash
DJANGO_SETTINGS_PROFILE=test poetry run python -m src.manage test src.accounts --verbosity=2
```

Expected: all tests pass including the 2 new ones.

- [ ] **Step 4: Commit**

```bash
git add src/accounts/tests.py
git commit -m "test(accounts): add tests for supporter star badge on profile page"
```
