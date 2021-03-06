#!/usr/bin/env python
import urlparse
import datetime as dt
import lxml.etree
import sys
import os
import name_tools

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pyutils.legislation import (LegislationScraper, Bill, Vote, Legislator,
                                 NoDataForYear)


import journal

def parse_ftp_listing(text):
    lines = text.strip().split('\r\n')
    return (' '.join(line.split()[3:]) for line in lines)


def chamber_name(chamber):
    if chamber == 'upper':
        return 'senate'
    else:
        return 'house'


class TXLegislationScraper(LegislationScraper):

    state = 'tx'

    metadata = {
        'state_name': 'Texas',
        'legislature_name': 'Texas Legislature',
        'upper_chamber_name': 'Senate',
        'lower_chamber_name': 'House of Representatives',
        'upper_title': 'Senator',
        'lower_title': 'Representative',
        'upper_term': 4,
        'lower_term': 2,
        'sessions': ['81'],
        'session_details': {
            '81': {'years': [2009, 2010], 'sub_sessions': ["811"]},
            }
        }

    def parse_bill_xml(self, chamber, session, txt):
        root = lxml.etree.fromstring(txt)
        bill_id = ' '.join(root.attrib['bill'].split(' ')[1:])
        bill_title = root.findtext("caption")

        if session[2] == 'R':
            session = session[0:2]

        bill = Bill(session, chamber, bill_id, bill_title)

        for action in root.findall('actions/action'):
            act_date = dt.datetime.strptime(action.findtext('date'),
                                            "%m/%d/%Y")

            extra = {}
            extra['action_number'] = action.find('actionNumber').text
            comment = action.find('comment')
            if comment is not None and comment.text:
                extra['comment'] = comment.text

            actor = {'H': 'lower',
                     'S': 'upper',
                     'E': 'executive'}[extra['action_number'][0]]

            bill.add_action(actor, action.findtext('description'),
                            act_date, **extra)

        for author in root.findtext('authors').split(' | '):
            if author != "":
                bill.add_sponsor('author', author)
        for coauthor in root.findtext('coauthors').split(' | '):
            if coauthor != "":
                bill.add_sponsor('coauthor', coauthor)
        for sponsor in root.findtext('sponsors').split(' | '):
            if sponsor != "":
                bill.add_sponsor('sponsor', sponsor)
        for cosponsor in root.findtext('cosponsors').split(' | '):
            if cosponsor != "":
                bill.add_sponsor('cosponsor', cosponsor)

        bill['subjects'] = []
        for subject in root.iterfind('subjects/subject'):
            bill['subjects'].append(subject.text.strip())

        return bill

    _ftp_root = 'ftp://ftp.legis.state.tx.us/'

    def scrape_bill(self, chamber, session, url):
        with self.urlopen_context(url) as data:
            bill = self.parse_bill_xml(chamber, session, data)
            bill.add_source(url)

            versions_url = url.replace('billhistory', 'billtext/html')
            versions_url = '/'.join(versions_url.split('/')[0:-1])

            bill_prefix = bill['bill_id'].split()[0]
            bill_num = int(bill['bill_id'].split()[1])
            long_bill_id = "%s%05d" % (bill_prefix, bill_num)

            with self.urlopen_context(versions_url) as versions_list:
                bill.add_source(versions_url)
                for version in parse_ftp_listing(versions_list):
                    if version.startswith(long_bill_id):
                        version_name = version.split('.')[0]
                        version_url = urlparse.urljoin(versions_url + '/',
                                                       version)
                        bill.add_version(version_name, version_url)

            self.save_bill(bill)

    def scrape_session(self, chamber, session):
        billdirs_path = '/bills/%s/billhistory/%s_bills/' % (
            session, chamber_name(chamber))
        billdirs_url = urlparse.urljoin(self._ftp_root, billdirs_path)

        with self.urlopen_context(billdirs_url) as bill_dirs:
            for dir in parse_ftp_listing(bill_dirs):
                bill_url = urlparse.urljoin(billdirs_url, dir) + '/'
                with self.urlopen_context(bill_url) as bills:
                    for history in parse_ftp_listing(bills):
                        self.scrape_bill(chamber, session,
                                         urlparse.urljoin(bill_url, history))

        if session in ['81R', '811']:
            journal_root = urlparse.urljoin(
                self._ftp_root, "/journals/" + session + "/html/", True)

            if chamber == 'lower':
                journal_root = urlparse.urljoin(journal_root,
                                                'house/', True)
            else:
                journal_root = urlparse.urljoin(journal_root,
                                                'senate/', True)

            with self.urlopen_context(journal_root) as listing:
                for name in parse_ftp_listing(listing):
                    if name.startswith('INDEX'):
                        continue
                    url = urlparse.urljoin(journal_root, name)
                    journal.parse(url, chamber, self)

    def scrape_bills(self, chamber, year):
        if int(year) < 2009 or int(year) > dt.date.today().year:
            raise NoDataForYear(year)

        # Expect the first year of a session
        if int(year) % 2 == 0:
            raise NoDataForYear(year)

        session_num = str((int(year) - 1989) / 2 + 71)
        subs = self.metadata['session_details'][session_num]['sub_sessions']

        self.scrape_session(chamber, session_num + 'R')
        for session in subs:
            self.scrape_session(chamber, session)

    def scrape_legislators(self, chamber, year):
        if year != '2009':
            raise NoDataForYear(year)

        if chamber == 'upper':
            self.scrape_senators(year)
        else:
            self.scrape_reps(year)

    def scrape_senators(self, year):
        senator_url = 'http://www.senate.state.tx.us/75r/senate/senmem.htm'
        with self.urlopen_context(senator_url) as page:
            root = lxml.etree.fromstring(page, lxml.etree.HTMLParser())

            for el in root.xpath('//table[@summary="senator identification"]'):
                sen_link = el.xpath('tr/td[@headers="senator"]/a')[0]
                full_name = sen_link.text
                district = el.xpath('string(tr/td[@headers="district"])')
                party = el.xpath('string(tr/td[@headers="party"])')

                pre, first, last, suffixes = name_tools.split(full_name)

                leg = Legislator('81', 'upper', district, full_name,
                                 first, last, '', party,
                                 suffix=suffixes)
                leg.add_source(senator_url)

                details_url = ('http://www.senate.state.tx.us/75r/senate/' +
                               sen_link.attrib['href'])
                with self.urlopen_context(details_url) as details_page:
                    details = lxml.etree.fromstring(details_page,
                                                    lxml.etree.HTMLParser())

                    comms = details.xpath("//h2[contains(text(), 'Committee Membership')]")[0]
                    comms = comms.getnext()
                    for comm in comms.xpath('li/a'):
                        comm_name = comm.text
                        if comm.tail:
                            comm_name += comm.tail

                        leg.add_role('committee member', '81',
                                     committee=comm_name.strip())

                self.save_legislator(leg)

    def scrape_reps(self, year):
        rep_url = 'http://www.house.state.tx.us/members/welcome.php'
        with self.urlopen_context(rep_url) as page:
            root = lxml.etree.fromstring(page, lxml.etree.HTMLParser())

            for el in root.xpath('//form[@name="frmMembers"]/table/tr')[1:]:
                full_name = el.xpath('string(td/a/font/span)')
                district = el.xpath('string(td[2]/span)')
                county = el.xpath('string(td[3]/span)')

                if full_name.startswith('District'):
                    # Ignore empty seats
                    continue

                pre, first, last, suffixes = name_tools.split(full_name)
                party = ''

                leg = Legislator('81', 'lower', district,
                                 full_name, first, last,
                                 '', party, suffix=suffixes)
                leg.add_source(rep_url)

                # Is there anything out there that handles meta refresh?
                redirect_url = el.xpath('td/a')[0].attrib['href']
                redirect_url = ('http://www.house.state.tx.us/members/' +
                                redirect_url)
                details_url = redirect_url
                with self.urlopen_context(redirect_url) as redirect_page:
                    redirect = lxml.etree.fromstring(redirect_page,
                                                     lxml.etree.HTMLParser())

                    try:
                        filename = redirect.xpath(
                            "//meta[@http-equiv='refresh']"
                            )[0].attrib['content']

                        filename = filename.split('0;URL=')[1]

                        details_url = details_url.replace('welcome.htm',
                                                          filename)
                    except:
                        # The Speaker's member page does not redirect.
                        # The Speaker is not on any committees
                        # so we can just continue with the next member.
                        self.save_legislator(leg)
                        continue


                with self.urlopen_context(details_url) as details_page:
                    details = lxml.etree.fromstring(details_page,
                                                    lxml.etree.HTMLParser())

                    comms = details.xpath(
                        "//b[contains(text(), 'Committee Assignments')]/"
                        "..//a")
                    for comm in comms:
                        leg.add_role('committee member', '81',
                                     committee=comm.text.strip())

                self.save_legislator(leg)

if __name__ == '__main__':
    TXLegislationScraper.run()
