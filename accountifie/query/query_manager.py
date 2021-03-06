import datetime
import pandas as pd
import numpy as np
import itertools
from dateutil.parser import parse
from functools import partial
import query_manager_strategy_factory

from django.conf import settings
from collections import defaultdict

import accountifie._utils
import accountifie.gl.api

class QueryManager:

    def __init__(self, gl_strategy=None):
        strategy_inst = query_manager_strategy_factory.QueryManagerStrategyFactory().get()
        if gl_strategy:
            if isinstance(gl_strategy, str):
                strategy_inst = query_manager_strategy_factory.QueryManagerStrategyFactory().get(strategy=gl_strategy)
            else:
                strategy_inst = gl_strategy
        self.gl_strategy = strategy_inst

    # CALCULATIONS
    
    ## REFERENCES: 0 internal, 1 external
    def cp_balances(self, company_id, acct_list,from_date=None,to_date=None):
        cache = glcache.get_cache(company_id)
        entries = cache.get_gl_entries(acct_list)
    
        if from_date:
            if type(from_date) == str:
                from_date = parse(from_date).date()
            entries = entries[entries['date'] >= from_date]
    
        if to_date:
            if type(to_date) == str:
                to_date = parse(to_date).date()
            entries = entries[entries['date'] <= to_date]
    
        entries['pos_amount'] = entries['amount'].map(abs)
        entries['neg_amount'] = entries['amount'].map(abs) * (-1.0)
        entries.rename(columns={'amount': 'total'}, inplace=True)
    
        bals = entries[['total', 'pos_amount', 'neg_amount', 'counterparty']].groupby('counterparty').sum()
        return bals[bals['total'] != 0.0].sort_index(by='total')
    
    ## REFERENCES: 0 internal, 2 external
    def path_drilldown(self, company_id, dates, path, excl_contra=None, excl_interco=None):
        paths = accountifie.gl.api.get_child_paths(path)
        output = self.pd_path_balances(company_id, dates, paths, excl_contra=excl_contra, excl_interco=excl_interco)
        return output
    
    ## REFERENCES: 1 internal, 0 external
    def deprec_factor(self, row, start, end):
        if row['date_end'] is None or row['date_end'] == row['date']:
            return 1.0
        days_outside = float(max((row['date_end'] - end).days, 0) + max((start - row['date']).days, 0))
        expense_period = float((row['date_end'] - row['date']).days + 1)
        deprec_factor = float('1')
        if expense_period > 0.0:
            deprec_factor -= days_outside / expense_period
        return deprec_factor
    
    ## REFERENCES: 1 internal, 0 external
    def deprec_calcs(self, start, end, entries):
    
        sub_entries = entries[(entries.date<=end) & (entries.date_end >=start)]
        _deprec_factor = partial(self.deprec_factor, start=start, end=end)
    
        if  not sub_entries.empty:
            sub_entries.loc[:, 'deprec_factor'] = sub_entries.apply(_deprec_factor, axis=1)
            sub_entries.loc[:, 'comment'] = sub_entries.apply(lambda row: row['comment'] + (' (prorated)' if row['deprec_factor'] < 1.0 else ''), axis=1)
            sub_entries.loc[:, 'amount'] *= sub_entries['deprec_factor']
            sub_entries.loc[:, 'date'] = sub_entries.apply(lambda row: min(row['date_end'],end), axis=1)
        return sub_entries
    
    ## REFERENCES: 0 internal, 1 external
    def trans_detail(self, trans_list, company_id=accountifie._utils.get_default_company()):
        
        cache = glcache.get_cache(company_id)
        entries = cache.get_gl_entries_trans(trans_list)
    
        entries['date_end'] = entries.apply(lambda row: row['date_end'] if row['date_end'] is not None else row['date'],axis=1)
        start_date = min(min(entries['date']), min(entries['date_end']))
        end_date = max(max(entries['date']), max(entries['date_end']))
    
        start_mth = start_date.month
        start_yr = start_date.year
    
        end_mth = end_date.month
        end_yr = end_date.year
    
        dt = accountifie._utils.start_of_month(start_date.month, start_date.year)
        dates = {dt.isoformat(): dt}
    
        dt1 = start_date
        dt2 = end_date
        start_month=dt1.month
        end_months=(dt2.year-dt1.year)*12 + dt2.month+1
        date_list = [accountifie._utils.end_of_month(mn,yr) for (yr, mn) in (
                  ((m - 1) / 12 + dt1.year, (m - 1) % 12 + 1) for m in range(start_month, end_months)
              )]
    
        date_list = list(set([start_date,end_date] + date_list))
        dates = dict((d.isoformat(), d) for d in date_list)
    
        dates[datetime.date(start_yr,start_month,1).isoformat()] = datetime.date(start_yr,start_month,1)
    
        data = {}
        for dt in dates:
            start, end = accountifie._utils.get_dates(dates[dt])
            sub_entries = self.deprec_calcs(start, end, entries)
            col = sub_entries[['account_id','amount']].groupby('account_id').sum()['amount']
            data[dt] = col[col!=0.0]
        return pd.DataFrame(data).fillna(0.0).T
    
    def pd_path_balances(self, company_id, dates, paths, filter_zeros=True, assets=False, excl_contra=None, excl_interco=False):

        path_accts = dict((p, [x['id'] for x in accountifie.gl.api.path_accounts({'path':p})]) for p in paths)
        acct_list = list(itertools.chain(*[path_accts[p] for p in paths]))
    
        dates_dict = dict((dt, accountifie._utils.get_dates_dict(dates[dt])) for dt in dates)
        date_indexed_account_balances = self.gl_strategy.account_balances_for_dates(company_id, acct_list, dates_dict, None, excl_interco, excl_contra)
    
        data = {}
        for dt in date_indexed_account_balances:
            account_balances = date_indexed_account_balances[dt]
    
            tuples = []
            for path in paths:
                path_sum = 0
                for account_id in path_accts[path]:
                    if account_id in account_balances:
                        path_sum -= account_balances[account_id]
                tuples.append((path, path_sum))
    
            data[dt] = dict(tuples)
    
        output = pd.DataFrame(data)
    
        if filter_zeros:
            output['empty'] = output.apply(lambda row: np.all(row.values == 0.0), axis=1)
            output = output[output['empty']==False]
            output.drop('empty', axis=1, inplace=True)
    
        # adjust assets sign
        if assets:
            asset_factor = output.index.map(lambda x: -1.0 if x[:6] == 'assets' else 1.0)
            for col in output.columns:
              output[col] = output[col] * asset_factor
        return output
    
    def pd_acct_balances(self, company_id, dates, paths=None, acct_list=None, excl_contra=None, excl_interco=False, cp=None):

        if not acct_list:
            if paths:
                accts = list(itertools.chain(*[accountifie.gl.api.path_accounts({'path':p}) for p in paths]))
                acct_list = [x['id'] for x in accts]
            else:
                acct_list = [x['id'] for x in accountifie.gl.api.path_accounts({'path':''})]

        dates_dict = dict((dt, accountifie._utils.get_dates_dict(dates[dt])) for dt in dates)
        
        with_counterparties = [cp.id] if cp else None
        date_indexed_account_balances = self.gl_strategy.account_balances_for_dates(company_id, acct_list, dates_dict, with_counterparties, excl_interco, excl_contra)
        
        # filter empties
        for dt in date_indexed_account_balances:
            d = date_indexed_account_balances[dt]
            date_indexed_account_balances[dt] = {k:d[k] for k in d if d[k] != 0.0}
    
        output = pd.DataFrame(date_indexed_account_balances)
        
        if paths:
            a = [[x['id'] for x in accountifie.gl.api.path_accounts({'path':path})] for path in paths]
            accts_list = list(itertools.chain(*a))
            return output[output.index.isin(acct_list)]
        elif acct_list:
            filtered_output = output[output.index.isin(acct_list)]
            return filtered_output

        return output
    
    def transaction_info(self, company_id, trans_id):
        return self.gl_strategy.get_transaction(company_id, trans_id)
    

    def transactions(self, company_id, from_date=settings.DATE_EARLY, to_date=settings.DATE_LATE):
        acct_list = [x['id'] for x in accountifie.gl.api.accounts({})]
        all_entries = self.gl_strategy.transactions(company_id, acct_list, from_date, to_date, 'end-of-month', None, None, None)

        return all_entries


    def pd_history(self, company_id, q_type, id, from_date=settings.DATE_EARLY, to_date=settings.DATE_LATE, excl_interco=None, excl_contra=None, incl=None, cp=None):
        if accountifie.gl.api.get_company({'company_id': company_id})['cmpy_type'] == 'CON':
            excl_interco = True
    
        if q_type == 'account':
            acct_list = [id]
        elif q_type == 'account_list':
            acct_list = incl
        elif q_type=='path':
            acct_list = [x['id'] for x in accountifie.gl.api.path_accounts({'path':id, 'excl': excl_contra, 'incl': incl})]
        else:
            raise ValueError('History not implemented for this type')

        with_counterparties = [cp] if cp else None

        all_entries = self.gl_strategy.transactions(company_id, acct_list, from_date, to_date, 'end-of-month', with_counterparties, excl_interco, excl_contra)
        
        if all_entries is None or len(all_entries) == 0:
            return pd.DataFrame()
    
        clean_end = lambda row: row['date_end'] if row['date_end'] else row['date']
        
    
        period_entries = []
    
        all_entries_pd = pd.DataFrame(all_entries)
        all_entries_pd.sort(['date', 'id'], ascending=[1, 0], inplace=True)
    
        cols = ['date', 'id', 'comment', 'account_id', 'contra_accts', 'counterparty', 'amount']
    
        top_line = pd.Series(dict((col, None) for col in cols))

        top_line['date'] = accountifie._utils.day_before(all_entries_pd.iloc[0]['date'])
        top_line['amount'] = 0.0
        top_line['comment'] = 'Opening Balance'

        
        all_entries_pd.reset_index(drop=True, inplace=True)
        all_entries_pd.loc['starting'] = top_line
        all_entries_pd.sort_index(by='date', axis=0, inplace=True)
        all_entries_pd['balance'] = all_entries_pd['amount'].cumsum()
    
        return all_entries_pd[cols + ['balance']]

    def balance_by_cparty(self, company_id, acct_ids, from_date=settings.DATE_EARLY, to_date=settings.DATE_LATE):

        all_entries = self.gl_strategy.transactions(company_id, acct_ids, from_date, to_date, 'end-of-month', None, None, None)
        if all_entries is None or len(all_entries) == 0:
            return pd.DataFrame()


        entries_tbl = pd.DataFrame(all_entries).sort_index(by='date')
        cp_tbl = entries_tbl[['counterparty','amount']].groupby('counterparty').sum()

        filter_small = lambda x: x>0.99 or x<-0.99
        cp_tbl = cp_tbl[cp_tbl['amount'].map(filter_small)]

        cp_tbl = cp_tbl.sort_index(by='amount', ascending=False)
        return cp_tbl['amount']
        


    




