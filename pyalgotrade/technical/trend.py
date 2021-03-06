# PyAlgoTrade
#
# Copyright 2011-2013 Gabriel Martin Becedillas Ruiz
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
.. moduleauthor:: Gabriel Martin Becedillas Ruiz <gabriel.becedillas@gmail.com>
"""

from pyalgotrade.technical import linreg
from pyalgotrade import warninghelpers

class Slope(linreg.Slope):
    def __init__(self, *args, **kwargs):
        # Deprecated since v0.15
        warninghelpers.deprecation_warning("Slope was moved in the pyalgotrade.technical.linreg package", stacklevel=2)
        linreg.Slope.__init__(self, *args, **kwargs)

class Trend(linreg.Trend):
    def __init__(self, *args, **kwargs):
        linreg.Trend.__init__(self, *args, **kwargs)
