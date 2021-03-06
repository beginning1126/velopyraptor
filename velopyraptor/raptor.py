"""
Copyright [2013] [James Absalon]

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""
import copy
import math
import matrix
import networkx
import numpy
from bitarray import bitarray

import config
import distributions.degree as degree
import distributions.gray as gray
import distributions.half as half
import distributions.optimal_esi as optimal_esi
import distributions.primes as primes
import distributions.random as random
from distributions.systematic_index import systematic_index
from schedule import Schedule

MIN_K = 4
MAX_K = 8192

if config._64BIT:
    DTYPE = 'uint64'
else:
    DTYPE = 'uint32'

class RaptorR10ParameterException(Exception):

    """
    Indicates a problem defining paramters from k
    """

    def __init__(self, message):
        """
        Constructor.  Simply calls super on parent class

        Arguments:
        message -- String message
        """
        super(RaptorR10ParameterException, self).__init__(message)

class RaptorR10DecodingScheduleException(Exception):
    """
    Indicates a problem occurred during creation of the
    decoding schedule
    """

    def __init__(self, specific):

        """
        Initializes the exception combining the specific problem to
        the generic problem
        """

        generic = "A problem occurred while creating the decoding schedule"
        super(RaptorR10DecodingScheduleException, self).__init__(
            "%s: %s" % (generic, specific)
        )

class RaptorR10(object):

    """
    Instance of this class is a Raptor R10 encoder.
    Raptor R10 is a systematic encoder.  That is the set of source
    symbols that are encoded are also the encoding first symbols
    produced while encoding.

    Encoding and Decoding are the same.  You build a matrix A
    from the known encoding symbols and reduce a to an identity
    matrix.  The operations performed on a are then performed
    on the known symbols to produce the intermediate symbols
    """

    def __init__(self, k, use_prepass=True, use_optimal_esis=False):
        """
        Arguments:
        k -- Integer representing number of source symbols.
            All other parameters depend upon k

        Keyword Arguments:
        use_prepass -- Boolean Sets wether or not a prepass should be made
        use_optimal_esis -- Attempts to produce only symbols requiring
            the least amount of XORS.
        """
        self.set_params(k)

        # Set prepass
        self.use_prepass = use_prepass

        # Set optimal_symbols
        self.use_optimal_esis = use_optimal_esis

        # Initialized current id to 0 - this will be incremented when
        # next() is called
        self.current_id = 0

        # this should be a list tuples consisting of (id, content)
        self.symbols = []

    def _get_next_id(self):
        """
        Returns the next id to produce the next encoded symbol
        Returns an integer
        """
        if self.use_optimal_esis:
            r = optimal_esi.get_esi(self.k, self.current_id)
        else:
            r = self.current_id
        self.current_id += 1
        return r

    def next(self):
        """
        Returns the next encoded symbol
        Return is a tuple (symbol id, bitarray)
        """
        symbol_id = self._get_next_id()
        return symbol_id, self.ltenc(symbol_id)

    def set_params(self, k):
        """
        Determines the parameters of the R10 encoder using k

        Arguments:
        k -- Integer number of source symbols
        """
        self.k = k

        if self.k < MIN_K or self.k > MAX_K:
            raise RaptorR10ParameterException(
                "k: %s -- k must be between %s and %s" % (
                    self.k,
                    MIN_K,
                    MAX_K
                )
            )
             
        # Let X be the smallest positive integer such that X*(X-1) >= 2*K.
        # a = 1, b =-1 c = -2(k)
        # quadratic = ((b * -1) + math.sqrt((b * b) - (4 * a * c))) / (2 * a)
        c = -2 * self.k
        self.x = ((1) + math.sqrt((1) - (4 * c))) / (2)
        self.x = int(math.ceil(self.x))

        # Let S be the smallest prime integer such that S >= ceil(0.01*K) + X
        self.s = primes.next(math.ceil(0.01 * self.k) + self.x)
        if not self.s:
            raise RaptorR10ParameterException(
                "s: -- No s found for k: %s and x: %s" % (self.k, self.x)
            )

        # Let H be the smallest integer such that choose(H,ceil(H/2)) >= K + S
        self.h = half.next(self.k + self.s)
        if not self.h:
            RaptorR10ParameterException(
                "h: Unable to find h for k: %s and s: %s" % (self.k, self.s)
            )

        # Let H' be ceil(H/2)
        self.h_prime = int(math.ceil(self.h / 2.0))

        # Let L be k + s + h
        self.l = self.k + self.s + self.h

        # Let L' be the smallest prime number such that L' >= L
        self.l_prime = primes.next(self.l)

        # Choose a systematic index based upon k.
        self.systematic_index = systematic_index[self.k]

    def __str__(self):
        """
        String representation of this instance of raptor r10
        Returns a string
        """
        result = "Raptor R10:"
        result += "\nK: %s" % self.k
        result += "\nX: %s" % self.x
        result += "\nS: %s" % self.s
        result += "\nH: %s" % self.h
        result += "\nL: K + S + H: %s" % self.l
        result += "\nL Prime: %s" % self.l_prime
        return result

    def triple(self, id):
        """
        Calculates and returns 3 tuple (d, a, b) for an id
        This tuple will be used to describe which intermediate symbols
        are XOR'd together to produce the id'th encoded symbol

        Arguments:
        id -- Integer that ids the id'th encoded symbol
        """
        Q = 65521
        A = (53591 + self.systematic_index * 997) % Q
        B = 10267 * (self.systematic_index + 1) % Q
        Y = (B + id * A) % Q
        v = random.R10(Y, 0, 1048576)
        d = degree.R10(v)
        a = 1 + random.R10(Y, 1, self.l_prime - 1)
        b = random.R10(Y, 2, self.l_prime)
        return (d, a, b)

    def calculate_d(self):
        """
        Doesnt really do much except s + h 0 symbols
        to the source block

        Returns a matrix prepended by s + h zero rows
        and the symbols
        """
        d = []
        symbolsize = len(self.symbols[0][1])

        # Creates the first s + h 0 rows of length symbolsize
        zeros = numpy.zeros(symbolsize, dtype=DTYPE)
        for i in xrange(self.s + self.h):
            d.append(numpy.array(zeros, copy=True))

        # Append the symbols that we do have
        for id, symbol in self.symbols:
            d.append(symbol)
        return d

    def ltenc(self, id):
        """
        Performs the ltencoding
        Symbols are produced by xoring a set of intermediate symbols together

        Arguments:
        id -- Integer that indicates the id'th symbol is to be encoded

        Returns a numpy array
        """
        
        d, a, b = self.triple(id)
        while b >= self.l:
            b = (b + a) % self.l_prime

        result = numpy.array(self.i_symbols[b], copy=True)

        for j in xrange(1, min(d, self.l)):
            b = (b + a) % self.l_prime
            while b >= self.l:
                b = (b + a) % self.l_prime
            self.xor_arrays(self.i_symbols[b], result)
        return result

    def min_degree_row(self, a, o_degrees, m, i, u, rows_with_r):
        """
        Chooses a minimum degree row out of rows with r

        Arguments:
        a           -- List of bitarrays representing matrix a
        o_degrees   -- List of original row degrees
        m           -- Integer n + s + h(a should have m rows)
        i           -- Integer representing the i'th
                       iteration in reducing matrix V
        u           -- Integer representing number of columns in matrix U
        rows_with_r -- List of row columns sharing the same number of ones
            in matrix V
        """

        min_degree = m + 1
        min_row = None
        for r in rows_with_r:
            if o_degrees[r] < min_degree:
                min_degree = o_degrees[r]
                min_row = r
        return min_row

    def row_from_graph(self, a, m, i, u, rows_with_r):
        """
        Builds a graph from rows where rows are edges and columns are vertices.
        Then chooses the first edge from the largest component

        Arguments:
        a           -- List of bitarrays representing matrix A
        m           -- Integer total number of rows in A
        i           -- Integer representing i'th iteration of reducing V
        u           -- Integer representing number of columns in u
        rows_with_r -- List of row indexes that share the same number of ones
                       in V
        """
        graph = networkx.Graph()
        for row in rows_with_r:
            vertices = []
            for vertex in xrange(i, self.l - u):
                if a[row][vertex]:
                    vertices.append(vertex)
            v1, v2 = tuple(vertices)
            graph.add_edge(v1, v2, row_index=row)

        # Calculate components in graph
        components = networkx.connected_component_subgraphs(graph)

        # Find the max component
        max_component = None
        max_size = 0
        for c in components:
            edges = c.edges(data=True)
            if len(edges) > max_size:
                max_component = edges
                max_size = len(edges)

        _, _, data = max_component[0]
        row = data['row_index']
        return row

    def rows_with_min_r(self, a, m, i, u):
        """
        Returns a tuple with the minimum number of 1s in a row in v
        and the indexes of the rows containing that number of 1s

        Arguments:
        a -- List of bitarrays representing the matrix A
        m -- Integer total number of rows in A
        i -- Integer indicating i'th iteration of reducing V in A
        u -- Integer number of columns in matrix U

        Returns tuple (minimum r, list of rows with minimum r)
        """
        # Find minimum number of ones in a row in sub matrix v.
        min_r = None
        rows_with_min_r = []
        for row_index in xrange(i, m):
            v_row = a[row_index][i:(self.l - u)]

            # let r be the number of ones in a row in v
            r = v_row.count()

            # Ignore rows 
            if r == 0:
                continue

            if min_r is None or r < min_r :
                min_r = r
                rows_with_min_r = [row_index]
            elif r == min_r:
                rows_with_min_r.append(row_index)

        return min_r, rows_with_min_r

    def calculate_i_symbols(self):
        """
        Calculates list of intermediate symbols.
        Applies the raptor decoding process to prevent multiplying the inverse
        of a by the source symbols

        Returns list of numpy arrays representing intermediate symbols
        """

        if len(self.symbols) < self.k:
            raise RaptorR10DecodingScheduleExceptionException(
                "Need at least %s symbols decode but only have %s." %
                (self.k, len(self.symbols))
            )

        a = self.a()
        schedule = self.decoding_schedule(a)

        D = self.calculate_d()

        self.xors = len(schedule.xors)
        self.i_symbols = [None for i in xrange(self.l)]
        for xor_row, target_row in schedule.xors:
            self.xor_arrays(D[xor_row], D[target_row])

        for i in xrange(self.l):
            self.i_symbols[schedule.c[i]] = D[schedule.d[i]]

    def decoding_schedule(self, a):
        """
        Applies a raptor decoding process to matrix a to reduce a
        to the identity matrix.  The operations taken on a infer the operations
        applied to the source to obtain the intermediate symbols.

        Returns a decoding schedule for a
        """
        m = self.s + self.h + len(self.symbols)
        schedule = Schedule(self.l, (self.s + self.h + len(self.symbols)))

        # Original degrees
        o_degrees = [row.count() for row in a]

        # Take a quick stab at trying to reduce the number of xors
        if self.use_prepass:
            self.prepass(a, schedule)

        # V is defined as the last (m - i rows and columns i through l - u)
        i = 0
        u = 0

        # Keep iterating until matrix V is gone leaving, I, U, and zero sub
        # matrices
        while (i + u) < self.l:

            r, rows_with_r = self.rows_with_min_r(a, m, i, u)
            if r == 2:
                row = self.row_from_graph(a, m, i, u, rows_with_r)
            else:
                row = self.min_degree_row(a, o_degrees, m, i, u, rows_with_r)

            if r == 0:
                raise RaptorR10DecodingScheduleException(
                    "No nonzero row to choose from v"
                )

            # Exchange row with first row of v
            self.exchange_row(a, o_degrees, i, row, schedule)

            # Reorder columns -- place a 1 in first column of v,
            # place remaining ones in right side of v by reordering columns
            # locate 1s
            ones = set()
            [ones.add(column) for column in xrange(i, self.l - u) if a[i][column]]

            # Exchange column i with first one column
            if not a[i][i]:
                column = ones.pop()
                self.exchange_column(a, i, column, schedule)
            else:
                ones.remove(i)

            # Align the rest up to the right
            column = self.l - u - 1
            while column > i and len(ones) > 0:
                if not a[i][column]:
                    self.exchange_column(a, column, ones.pop(), schedule)
                else:
                    ones.remove(column)
                column -= 1

            # XOR all rows below a[i][i] that have 1
            for row in xrange(i + 1, m):
                if a[row][i]:
                    self.xor_row(a, row, i, schedule)
            i += 1
            u += r - 1

        # matrix u is divided into the first i rows u_upper and m-i rows u_lower
        # perform gaussian elimination on u_lower so that the first u rows are
        # a u identity matrix
        for column in xrange(self.l - u, self.l):
            if not a[column][column]:
                # find a row to swap
                for row in xrange(column + 1, m):
                    if a[row][column]:
                        # swap rows row and column
                        self.exchange_row(a, o_degrees, column, row, schedule)
                        break

                if not a[column][column]:
                    raise RaptorR10DecodingScheduleException(
                        "U lower is of less rank than %s." % u
                    )

            # Loop down through rows below column xoring row column
            for row in xrange(column + 1, m):
                if a[row][column]:
                    self.xor_row(a, row, column, schedule)

        # U upper should now be in upper triangular form. now attack the top
        for column in xrange(self.l - 1, self.l - u - 1, -1):
            for row in xrange(i, column):
                if a[row][column]:
                    self.xor_row(a, row, column, schedule)

        # Discard any rows left after l
        a = a[:self.l]
        # a should now be l x l

        # XOR to get rid of 1s in U_Upper
        for row in xrange(i):
            for column in xrange(self.l - u, self.l):
                if a[row][column]:
                    self.xor_row(a, row, column, schedule)

        return schedule

    def a(self):
        """
        Calculates the matrix a by constructing the submatrices
        and appending them together

        Returns a list of bitarrays representing a
        """

        # Init a to the empty list
        a = []

        # Create the ldpc section
        a.extend(self.ldpc_section())

        # Create the hdpc section
        a.extend(self.hdpc_section())

        # Create the lt section
        a.extend(self.lt_section())
        return a

    def ldpc_section(self):
        m = []
        # First vertical section
        # (s x k)ldpc | (s x s)identity | (s x h)zero matrix
        ldpc = self.ldpc(self.k, self.s)
        identity = matrix.identity(self.s)
        zero = matrix.zeros(self.s, self.h)
        for i in xrange(self.s):
            m.append(ldpc[i] + identity[i] + zero[i])
        return m

    def hdpc_section(self):
        m = []
        # (h x (k + s)) half | (h x h)identity
        half = self.half(self.k, self.s, self.h, self.h_prime)
        identity = matrix.identity(self.h)
        for i in xrange(self.h):
            m.append(half[i] + identity[i])
        return m

    def lt_section(self):
        m = []
        # Third vertical section
        # (k x l) lt
        for id, symbol in self.symbols:
            m.append(self.lt_row(id))
        return m

    def lt_row(self, esi):
        """
        Creates a k x l matrix representing xor operations on
        the intermediate symbols

        Arguments:
        l -- (k + s + h) such that s and h satisfy precoding relationships
        l_prime -- next prime number after l
        triples -- set of k source triples (d, a, b)
                    d[i] = triples[i][0]
                    a[i] = triples[i][1]
                    b[i] = triples[i][2]

        Returns a list of bitarrays representing G_LT
        """
        # ba[n] will be k1 if and only if c[b] is used in the xoring of LTEnc
        ba = bitarray(self.l)
        ba.setall(False)
        d, a, b = self.triple(esi)

        while b >= self.l:
            b = (b + a) % self.l_prime

        ba[b] = True

        for j in xrange(1, min(d, self.l)):
            b = (b + a) % self.l_prime
            while b >= self.l:
                b = (b + a) % self.l_prime
            ba[b] = True
        return ba

    def can_decode(self):
        """
        Determines whether or not decoding can take place

        Returns true for success, false otherwise
        """        
        try:
            a = self.a()
            self.decoding_schedule(a)
            return True
        except:
            pass
        return False

    @classmethod
    def prepass(cls, a, schedule):

        """
        Taking a stab at doing some preliminary xoring before
        calculating the schedule as normal in an effort to reduce
        the number of XORs.

        Makes a single pass comparing rows to other rows and determining
        if the xoring of two rows results in less ones.

        Arguments:
        a - List of bitarrays representing matrix a
        schedule - Schedule of operations recorded on a to mimic on
            encoded symbols
        """

        # Iterate over rows in a
        for i in xrange(len(a)):

            # Iterate over remaining rows in a
            for j in xrange(i + 1, len(a)):
                count = a[j].count()
                new_row = a[i] ^ a[j]

                # Check requirements prior to proceeding with XOR
                if new_row.count() + 2 < count:
                    cls.xor_row(a, j, i, schedule)
 
    @classmethod
    def xor_row(cls, a, r1, r2, schedule):

        """
        XORS r2 into r1 and records the operation
        within the schedule

        Arguments:
        a -- List of l sized bitarrays
        r1 -- Integer target row id
        r2 -- Integer source row id
        schedule -- Schedule to record the operation in
        """
        # XOR r2 of a into r1 of a
        a[r1] ^= a[r2]

        # Schedule the xor
        schedule.xor(r1, r2)

    @classmethod
    def exchange_column(cls, a, c1, c2, schedule):
        """
        Exchanges column c1 of a with column c2 of a and records the operation
        in the schedule

        Arguments:
        a -- List of bit arrays representing a
        c1 -- Integer first column id
        c2 -- Integer second column id
        schedule -- Schedule of operations performed upon a        
        """
        # Exchange the columns c1 and c2 in a
        for i in xrange(len(a)):
            temp = a[i][c1]
            a[i][c1] = a[i][c2]
            a[i][c2] = temp

        # Record the operation
        schedule.exchange_column(c1, c2)

    @classmethod
    def exchange_row(cls, a, o_degrees, r1, r2, schedule):
        """
        Exchanges row r1 of a with row r2 of a and records the operation
        in the schedule

        Arguments:
        a -- list of bitarrays representing a
        o_degrees -- List of original degrees of rows
        r1 -- Integer id of first row to exchange
        r2 -- Integer id of second row to exchange
        schedule -- Schedule to record the operation in
        """
        # Exchange r1 with r2 of a
        temp = a[r1]
        a[r1] = a[r2]
        a[r2] = temp

        temp = o_degrees[r1]
        o_degrees[r1] = o_degrees[r2]
        o_degrees[r2] = temp

        # Record the operation
        schedule.exchange_row(r1, r2)

    @classmethod
    def ldpc(cls, k, s):
        """
        Generates an ldpc matrix base upon parameters to the
        raptor R10 coding
        Arguments:
        k -- Integer k Number of source symbols
        s -- Integer s based upon k that satisfies R10 precoding relationships

        Returns a list of bitarrays representing G_LDPC
        """
        matrix = []

        for i in xrange(s):
            ba = bitarray(k)
            ba.setall(False)
            matrix.append(ba)

        for i in xrange(k):
            a = int(1 + (math.floor(i / float(s)) % (s - 1)))
            b =  i % s
            matrix[b][i] = True
            b = (b + a) % s
            matrix[b][i] = True
            b = (b + a) % s
            matrix[b][i] = True
        return matrix

    @classmethod
    def half(cls, k, S, H, H_HALF):
        """
        Generates the half matrix based upon the gray sequence.
        Arguments are capped to look similar to loops on rfc 5053 specs
        Arguments:
        k     -- Integer k
        S     -- Integer S based upon k that satisifies precoding relationships
        H     -- Integer H based upon k that satisifies precoding relationships
        H_HALF -- Integer H_HALF(should be rea din rfc 5053 as H') that
            satisifies precoding relationships

        Returns a list of bitarrays representing G_HDPC
        """

        def check_nth_bit(n, number):
            mask = 1
            if (mask << n) & number:
                return True
            return False

        matrix = []
        for h in xrange(H):
            ba = bitarray(k + S)
            ba.setall(False)
            for j in xrange(k + S):
                if check_nth_bit(h, gray.SEQUENCE[H_HALF][j]):
                    ba[j] = True
            matrix.append(ba)
        return matrix

    @classmethod
    def xor_arrays(cls, source, target):
        """
        Moved to its own function for profiling purposes.
        May want to consider moving out to inline.
        The source array will be xored into place into
        the target array

        Arguments:
        source -- Numpy array source array
        target -- Numpy array target array
        """
        numpy.bitwise_xor(source, target, target)

    def gen_optimal_symbols(self, how_many):
        """
        Used in generating the sequences of optimal symbols.
        Optimal symbols should be pulled from a look up table.  The function
        should really only be used to populate that table.

        Arguments:
        how_many -- Integer number of optimal symbols to produce
        """
        a = []
        a.extend(self.ldpc_section())
        a.extend(self.hdpc_section())

        yielded = 0
        xors = 1
        i = 0
        while yielded < how_many:
            ba = self.lt_row(i)
            if ba.count() == xors and not(ba in a):
                yielded += 1
                a.append(ba)
                yield i
            i += 1
            if i == 5000:
                i = 0
                xors += 1

