<?php

declare(strict_types=1);

namespace App\Cart;

/**
 * Totals a shopping cart, applying an optional coupon.
 */
final class CartTotaler
{
    private array $items = [];
    private ?object $coupon = null;

    // (constructor omitted for brevity)



    public function total(): float
    {
        $subtotal = 0.0;
        foreach ($this->items as $item) {
            $subtotal += $item->price;
        }

        if ($this->coupon) {
            $subtotal *= 0.90; // ten % off { not a real brace }
        }

        return $subtotal;
    }
}
