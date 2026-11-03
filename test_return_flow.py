import unittest
from datetime import date
import pandas as pd
from return_flow import build_return_plan


def main_rows(rows):
    return pd.DataFrame(rows, columns=['Sklad', 'Lokace', 'Produkt', 'Množstvo na lokácií', 'Predajnosť'])


def location_rows(rows):
    frame = pd.DataFrame(rows, columns=['Sklad', 'Lokace', 'Produkt', 'Množstvo'])
    frame['Skladom'] = 99999  # Repeated SKU totals must never become location stock.
    return frame


class ReturnFlowTests(unittest.TestCase):
    def calculate(self, main, swap, remote, horizon=60):
        return build_return_plan(main, swap, remote, 30, 5, 7, horizon, date(2026, 11, 3))

    def test_zones_do_not_double_count_and_return_whole_location(self):
        main = main_rows([
            ('A', 'P1', 'X', 100, 300), ('A', '2-SWAP-01', 'X', 50, 300),
            ('A', '4D1-01', 'X', 80, 300), ('A', 'SHUTTLE-SWAP', 'X', 70, 300),
        ])
        swap = location_rows([('A', '2-SWAP-01', 'X', 50), ('A', 'SHUTTLE-SWAP', 'X', 70)])
        remote = location_rows([('A', '4D1-01', 'X', 80), ('A', 'SHUTTLE-SWAP', 'X', 70)])
        summary, plan, *_ = self.calculate(main, swap, remote)
        row = summary.iloc[0]
        self.assertEqual((row['Primár (ks)'], row['SWAP (ks)'], row['4DS (ks)']), (100, 50, 150))
        self.assertEqual(row['Primár vydrží (dni)'], 10)
        self.assertEqual(row['SWAP samostatne (dni)'], 5)
        self.assertEqual(row['Primár + SWAP vydrží (dni)'], 15)
        self.assertEqual(row['Prvý návrat najneskôr'], date(2026, 11, 11))
        self.assertEqual(plan['Presunúť (ks)'].tolist(), [80, 70])
        self.assertEqual(plan['O dní'].tolist(), [8, 16])

    def test_mixed_location_comes_back_once_with_all_products(self):
        main = main_rows([('A', 'P1', 'X', 10, 300), ('A', 'P2', 'Y', 1000, 300)])
        remote = location_rows([('A', '4D1-MIX', 'X', 20), ('A', '4D1-MIX', 'Y', 40),
                                ('A', '4D1-MIX', 'X', 30)])
        _, plan, *_ = self.calculate(main, location_rows([]), remote)
        self.assertEqual(len(plan), 2)
        self.assertEqual(plan['O dní'].tolist(), [0, 0])
        self.assertEqual(plan['Poradie lokácie'].tolist(), [1, 1])
        self.assertEqual(dict(zip(plan['Produkt'], plan['Presunúť (ks)'])), {'X': 50, 'Y': 40})

    def test_missing_sales_blocks_entire_mixed_location(self):
        main = main_rows([('A', 'P1', 'X', 0, 300)])
        remote = location_rows([('A', '4D1-MIX', 'X', 20), ('A', '4D1-MIX', 'UNKNOWN', 40)])
        summary, plan, _, _, blocked = self.calculate(main, location_rows([]), remote)
        self.assertTrue(plan.empty)
        self.assertEqual(blocked, [('A', '4D1-MIX')])
        self.assertEqual(summary.set_index('Produkt').loc['UNKNOWN', 'Stav'], 'Chýba predajnosť')

    def test_zero_sales_fallback_and_outside_horizon(self):
        main = main_rows([('A', 'P1', 'X', 20, 0)])
        remote = location_rows([('A', '4D1-01', 'X', 40)])
        summary, plan, *_ = self.calculate(main, location_rows([]), remote)
        self.assertTrue(plan.empty)
        self.assertEqual(summary.iloc[0]['Primár vydrží (dni)'], 120)
        self.assertTrue(summary.iloc[0]['Náhradná predajnosť'])

    def test_invalid_quantities_and_conflicting_sales(self):
        for qty in [-1, 'bad', None]:
            with self.subTest(qty=qty), self.assertRaises(ValueError):
                self.calculate(main_rows([('A', 'P1', 'X', 20, 30)]), location_rows([]),
                               location_rows([('A', '4D1-01', 'X', qty)]))
        with self.assertRaisesRegex(ValueError, 'Rozdielna predajnosť'):
            self.calculate(main_rows([('A', 'P1', 'X', 20, 30), ('A', 'P2', 'X', 30, 60)]),
                           location_rows([]), location_rows([]))

    def test_location_warehouse_identity_case_and_decimal_comma(self):
        main = main_rows([('A', 'P1', ' X ', '1,5', 30)])
        remote = location_rows([('A', ' 4d1-01 ', 'X', '3,5'), ('B', '4D1-01', 'X', 2),
                                ('A', 'OTHER', 'X', 100)])
        summary, plan, _, ignored, _ = self.calculate(main, location_rows([]), remote)
        self.assertEqual(summary.iloc[0]['4DS (ks)'], 5.5)
        self.assertEqual(ignored, 1)
        self.assertEqual(plan['Presunúť (ks)'].sum(), 5.5)
        self.assertEqual(plan['Poradie lokácie'].nunique(), 2)


if __name__ == '__main__':
    unittest.main()
