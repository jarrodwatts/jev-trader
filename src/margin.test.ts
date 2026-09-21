import { test, expect } from "bun:test";
import { Market } from "./market";

const SIZE = 200;
const PRICE = 0.025; // 200 MON costs 5 USDC

const market = () => {
  const m = new Market();
  m.margin = { mon: 600, usdc: 15 };
  return m;
};

test("placing a bid debits usdc, placing an ask debits mon", () => {
  const m = market();
  m.lockQuote("buy", SIZE, PRICE);
  expect(m.margin.usdc).toBeCloseTo(10, 6);
  m.lockQuote("sell", SIZE, PRICE);
  expect(m.margin.mon).toBeCloseTo(400, 6);
});

test("a quote that never rests gives its margin back", () => {
  const m = market();
  m.lockQuote("buy", SIZE, PRICE);
  m.releaseQuote("buy", SIZE, PRICE);
  expect(m.margin.usdc).toBeCloseTo(15, 6);
});

test("a filled bid turns usdc into mon", () => {
  const m = market();
  m.lockQuote("buy", SIZE, PRICE);
  m.creditFill("buy", SIZE, PRICE);
  expect(m.margin.usdc).toBeCloseTo(10, 6);
  expect(m.margin.mon).toBeCloseTo(800, 6);
});

test("a filled ask turns mon into usdc", () => {
  const m = market();
  m.lockQuote("sell", SIZE, PRICE);
  m.creditFill("sell", SIZE, PRICE);
  expect(m.margin.mon).toBeCloseTo(400, 6);
  expect(m.margin.usdc).toBeCloseTo(20, 6);
});

// The live failure: three bids filled in a row leave too little usdc for a fourth, and the mirror
// has to show that immediately. Reading it only every 200 blocks is what burned gas on doomed orders.
test("consecutive filled bids drain usdc below one order", () => {
  const m = market();
  for (let i = 0; i < 3; i++) {
    m.lockQuote("buy", SIZE, PRICE);
    m.creditFill("buy", SIZE, PRICE);
  }
  expect(m.margin.usdc).toBeCloseTo(0, 6);
  expect(m.margin.usdc).toBeLessThan(SIZE * PRICE); // a fourth bid is unaffordable
  expect(m.margin.mon).toBeCloseTo(1200, 6);
});
