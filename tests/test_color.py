from __future__ import with_statement, print_function
import unittest
from plumbum import COLOR
from plumbum.color import with_color
from plumbum.color.color import Style


class TestColor(unittest.TestCase):

    def setUp(self):
        Style.use_color = True

    def testColorStrings(self):
        self.assertEqual('\033[0m', COLOR.RESET)
        self.assertEqual('\033[1m', COLOR.BOLD)
        self.assertEqual('\033[39m', COLOR.FG.RESET)

    def testNegateIsReset(self):
        self.assertEqual(COLOR.RESET, -COLOR)
        self.assertEqual(COLOR.FG.RESET, -COLOR.FG)
        self.assertEqual(COLOR.BG.RESET, -COLOR.BG)

    def testLoadColorByName(self):
        self.assertEqual(COLOR['LightBlue'], COLOR.FG['LightBlue'])
        self.assertEqual(COLOR.BG['light_green'], COLOR.BG['LightGreen'])
        self.assertEqual(COLOR['DeepSkyBlue1'], COLOR['#00afff'])
        self.assertEqual(COLOR['DeepSkyBlue1'], COLOR[39])

    def testMultiColor(self):
        sumcolor = COLOR.BOLD + COLOR.BLUE
        self.assertEqual(COLOR.RESET, -sumcolor)

    def testUndoColor(self):
        self.assertEqual('\033[39m', -COLOR.FG)
        self.assertEqual('\033[39m', ''-COLOR.FG)
        self.assertEqual('\033[49m', -COLOR.BG)
        self.assertEqual('\033[49m', ''-COLOR.BG)
        self.assertEqual('\033[21m', -COLOR.BOLD)
        self.assertEqual('\033[22m', -COLOR.DIM)
        for i in (1, 2, 4, 5, 7, 8):
            self.assertEqual('\033[%im' % i, -COLOR('\033[%im' % (20 + i)))
            self.assertEqual('\033[%im' % (i + 20), -COLOR('\033[%im' % i))
        for i in range(10):
            self.assertEqual('\033[39m', -COLOR('\033[%im' % (30 + i)))
            self.assertEqual('\033[49m', -COLOR('\033[%im' % (40 + i)))
            self.assertEqual('\033[39m', -COLOR.FG(i))
            self.assertEqual('\033[49m', -COLOR.BG(i))
        for i in range(256):
            self.assertEqual('\033[39m', -COLOR.FG[i])
            self.assertEqual('\033[49m', -COLOR.BG[i])
        self.assertEqual('\033[0m', -COLOR.RESET)
        self.assertEqual('\033[0m', -COLOR('this is random'))

    def testLackOfColor(self):
        Style.use_color = False
        self.assertEqual('', COLOR.FG.RED)
        self.assertEqual('', -COLOR.FG)
        self.assertEqual('', COLOR.FG['LightBlue'])

    def testVisualColors(self):
        print()
        for c in (COLOR.FG(x) for x in range(1, 6)):
            with with_color(c):
                print('Cycle color test', end=' ')
            print(' - > back to normal')
        with with_color():
            print(COLOR.FG.GREEN + "Green "
                  + COLOR.BOLD + "Bold "
                  - COLOR.BOLD + "Normal")
        print("Reset all")

    def testToggleColors(self):
        print()
        print(COLOR.FG.RED("this is in red"), "but this is not")
        print(COLOR.FG.GREEN + "Hi, " + COLOR.BG[23]
              + "This is on a BG" - COLOR.BG + " and this is not")
        COLOR.RESET()

if __name__ == '__main__':
    unittest.main()
