import stripe


def subscription(price_id):
    return stripe.Subscription.create(price_id=price_id)
