**Summary:** Active members are required to read and accept the Member Agreement, with settings controlled by admins.

**Area:** auth, settings and models

### Problem
Closes #469 part 1 of 2

### Solution
- Add `member_agreement_required` and `member_agreement_url` to `SiteConfiguration`.
- Create `MemberAgreementAcceptance` model to record members' acceptance.
- Add `MemberAgreementMiddleware` to intercept unaccepted member requests and route them to the prompt.
- Create `/agreement/` page rendering the agreement URL in an iframe.

### Impact / Risks
Migrations added for `SiteConfiguration` and new `MemberAgreementAcceptance` model. No existing behavior changes since it defaults to off.

### Verification
Manually verified the settings tab and middleware flow.
