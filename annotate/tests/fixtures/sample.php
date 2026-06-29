<?php
class Cart {
  private $items = [];
  function total() {
    $subtotal = 0;
    if ($this->coupon) {
      $subtotal *= 0.90; // ten % off { not a real brace }
    }
    return $subtotal;
  }
}
