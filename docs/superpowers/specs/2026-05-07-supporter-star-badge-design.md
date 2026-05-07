# Supporter Star Badge

**Date:** 2026-05-07
**Status:** Approved

## Summary

Users who donated/helped cover site costs receive a gold star icon next to their username on their profile page. Admin assigns the badge manually via Django admin.

## Model

Add `is_supporter = models.BooleanField(default=False)` to `UserProfile` in `src/accounts/models.py`.

- Default `False` — no existing user is affected
- Requires one new migration: `src/accounts/migrations/0006_userprofile_is_supporter.py`

## Admin

Modify `UserProfileAdmin` in `src/accounts/admin.py`:

- Add `is_supporter` to `list_display`
- Add `is_supporter` to `list_filter`
- Add `is_supporter` to `list_editable` — enables inline checkbox toggle from the list view without opening each record

## Template

In `src/penninicup/templates/penninicup/profile.html`, line 47 (next to `{{ profile_user.username }}`):

```html
<h2 class="text-2xl font-semibold break-all flex items-center gap-2">
    {{ profile_user.username }}
    {% if profile_obj.is_supporter %}
    <i data-lucide="star" class="inline h-5 w-5 text-yellow-400 fill-yellow-400"></i>
    {% endif %}
</h2>
```

- Uses Lucide `star` icon (already loaded in project)
- `fill-yellow-400` fills the icon solid gold
- No tooltip, no text label
- Renders for both owner and visitor views of the profile

## Tests

In `src/accounts/tests.py` (or new test class):

- `test_supporter_star_shown`: profile with `is_supporter=True` → response contains `data-lucide="star"`
- `test_supporter_star_hidden`: profile with `is_supporter=False` → response does not contain `data-lucide="star"`

Use Django test client to GET the profile URL for each case.

## Out of Scope

- Star does not appear in rankings, participant lists, or any other page
- No tooltip or label text
- No automated assignment (payment webhook, etc.) — manual admin only
- No audit trail of when/who granted the badge
