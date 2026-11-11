"""
Sektion endgueltig loeschen (nur globale Admins, im Portal): Touren mit allen
Buchungen, Huetten, Hüttenbuchungen, Logo und die Konten, die nur in dieser
Sektion Mitglied sind. Konten mit weiteren Mitgliedschaften verlieren nur
diese, globale Admins bleiben immer.
"""
from application.events.models import Event
from application.huts.models import Hut, HutBooking
from application.models.section import Section, ref_id
from application.models.user import User, remove_user_references


def blocked_reason(section):
    """Warum die Sektion nicht geloescht werden darf, sonst None"""
    if section.is_default:
        return "Die Standardsektion kann nicht gelöscht werden."
    if section.subscription_status in ('active', 'trialing', 'past_due') and not section.cancel_at_period_end:
        return "Das Abo läuft noch. Erst kündigen, sonst bucht Stripe weiter ab."
    return None


def _members(section):
    """(Konten, die geloescht werden, Konten, die nur die Mitgliedschaft verlieren)"""
    own = ref_id(section.pk)
    delete, keep = [], []
    for user in User.objects(memberships__section=section.pk):
        others = [m for m in user.memberships if ref_id(m._data.get('section')) != own]  # pylint: disable=protected-access
        (keep if others or user.global_admin else delete).append(user)
    return delete, keep


def contents(section):
    """Was beim Loeschen verschwindet, fuer die Bestaetigungsseite"""
    delete, keep = _members(section)
    return {
        'events': Event.all_objects(section=section.pk).count(),
        'huts': Hut.all_objects(section=section.pk).count(),
        'hut_bookings': HutBooking.all_objects(section=section.pk).count(),
        'users_deleted': len(delete),
        'users_kept': len(keep),
    }


def delete_section(section):
    """Alles der Sektion loeschen, gibt die Zahlen aus contents zurueck"""
    counts = contents(section)
    delete, keep = _members(section)
    event_ids = [event.pk for event in Event.all_objects(section=section.pk).only('id')]
    if event_ids:
        User.objects(event_registrations__in=event_ids).update(pull_all__event_registrations=event_ids)
        User.objects(favorites__in=event_ids).update(pull_all__favorites=event_ids)
    for user in keep:
        User.objects(id=user.id).update_one(pull__memberships__section=section.pk)
    for user in delete:
        remove_user_references(user)
        user.delete()
    HutBooking.all_objects(section=section.pk).delete()
    Hut.all_objects(section=section.pk).delete()
    Event.all_objects(section=section.pk).delete()
    Section.objects(partner_sections=section.pk).update(pull__partner_sections=section.pk)
    if section.logo_image:
        section.logo_image.delete()
    section.delete()
    return counts
