# Demo script

1. Start backend and frontend. Select Sales A and run the default inquiry: 1,000 units of Product A at 8%. Confirm `ALLOW`, `DISCOUNT-01@v1`, and a `$92,000.00` Draft.
2. Select Demo Admin and activate `DISCOUNT-01@v2`. Return to Sales A and rerun at 8%: confirm `DENY / DISCOUNT_LIMIT_EXCEEDED` and no quote. Change to 5%: confirm an allowed `$95,000.00` draft.
3. As Admin, activate `DISCOUNT-01@v3`; the direct limit is 5%, and the review limit is 8%. As Sales A, run the 8% inquiry. Confirm `REVIEW`, a pending approval, and no quote.
4. Select Demo Manager. Confirm the request in the queue and approve it. Confirm the result view shows one `$92,000.00` Draft created after server-side revalidation. A repeat approval returns the same Draft.
5. Export the quote CSV. Optionally compare v1 and v2 against the same inquiry; verify the Diff shows `ALLOW` versus `DENY` and leaves the active policy and quote count unchanged.
6. As Admin, upload a UTF-8 `.md`/`.txt` source. Confirm it remains a draft until structured limits are entered and explicitly saved/activated. Reset data and confirm v1 is active again.
7. As Sales B, claim VIP in an inquiry or try to read Sales A's quote. Confirm trusted customer data is used and cross-owner quote access is denied.

The actor switcher and no-credential model path are Demo features, not real authentication or a real provider integration.
