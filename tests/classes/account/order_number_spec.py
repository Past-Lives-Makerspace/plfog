import re

from classes.factories import RegistrationFactory


def describe_registration_order_number():
    def it_generates_on_save_in_pl_format(db):
        reg = RegistrationFactory()
        assert reg.order_number
        # PL-XXXX-YY where XXXX is from the unambiguous alphabet (no 0OI1)
        # and YY is 2 digits of the year suffix.
        assert re.match(r"^PL-[A-HJ-NP-Z2-9]{4}-\d{2}$", reg.order_number), reg.order_number

    def it_generates_unique_codes(db):
        regs = [RegistrationFactory() for _ in range(20)]
        codes = {r.order_number for r in regs}
        assert len(codes) == 20

    def it_does_not_overwrite_existing_value(db):
        reg = RegistrationFactory(order_number="PL-XXXX-99")
        reg.refresh_from_db()
        assert reg.order_number == "PL-XXXX-99"

    def it_does_not_regenerate_on_subsequent_saves(db):
        reg = RegistrationFactory()
        original = reg.order_number
        reg.amount_paid_cents = 1234
        reg.save()
        reg.refresh_from_db()
        assert reg.order_number == original
