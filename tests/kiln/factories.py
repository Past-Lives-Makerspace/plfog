"""Factories for kiln ticket specs (#691)."""

from __future__ import annotations

import factory
from factory.django import DjangoModelFactory

from kiln.models import ClayOption, GlazeOption, KilnFlag, KilnTicket, KilnTicketPhoto
from tests.membership.factories import MemberFactory


class ClayOptionFactory(DjangoModelFactory):
    class Meta:
        model = ClayOption

    name = factory.Sequence(lambda n: f"Spec Clay {n}")
    sort_order = 100


class GlazeOptionFactory(DjangoModelFactory):
    class Meta:
        model = GlazeOption

    name = factory.Sequence(lambda n: f"Spec Glaze {n}")
    sort_order = 100


class KilnTicketFactory(DjangoModelFactory):
    """A draft with no answers. Pass ``ready=True`` for a bisque ticket Submit accepts (photo included)."""

    class Meta:
        model = KilnTicket
        skip_postgeneration_save = True

    maker = factory.SubFactory(MemberFactory)

    @factory.post_generation
    def ready(obj, create, extracted, **kwargs):  # noqa: N805  # factory-boy hook, obj is the instance
        if not create or not extracted:
            return
        obj.firing_type = KilnTicket.FiringType.BISQUE
        obj.clay = obj.clay or ClayOptionFactory()
        obj.walls_under_inch = True
        obj.save()
        KilnTicketPhotoFactory(ticket=obj, is_cover=True)


class KilnTicketPhotoFactory(DjangoModelFactory):
    class Meta:
        model = KilnTicketPhoto

    ticket = factory.SubFactory(KilnTicketFactory)
    image = factory.django.ImageField(width=8, height=8, color="brown", filename="pot.jpg")
    tile = factory.django.ImageField(width=4, height=4, color="brown", filename="pot-tile.jpg")
    is_cover = False


class KilnFlagFactory(DjangoModelFactory):
    class Meta:
        model = KilnFlag

    ticket = factory.SubFactory(KilnTicketFactory)
    kind = KilnFlag.Kind.OTHER_CLAY
