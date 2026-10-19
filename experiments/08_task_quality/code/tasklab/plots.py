import csv
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from .common import ROOT,TASKS
def plot():
 with (ROOT/'tables/caf-boxplot-data.csv').open() as f:rows=list(csv.DictReader(f))
 plt.rcParams.update({'font.family':'serif','font.serif':['DejaVu Serif'],'font.size':10,'axes.spines.top':False,'axes.spines.right':False,'pdf.fonttype':42,'ps.fonttype':42})
 fig,axes=plt.subplots(1,3,figsize=(10,3.1),sharey=True,layout='constrained')
 names=['HotpotQA','GovReport','TriviaQA','Synthetic\n5 questions × 6']
 for ax,f in zip(axes,[.1,.25,.5]):
  series=[[float(r['caf_cache']) for r in rows if r['task']==task and float(r['cache_price_fraction'])==f] for task in TASKS]
  boxes=ax.boxplot(series,tick_labels=names,patch_artist=True,widths=.55,medianprops={'color':'#111111','linewidth':1.5})
  for patch,color in zip(boxes['boxes'],['#264653','#2A9D8F','#E9C46A','#E76F51']):patch.set_facecolor(color);patch.set_alpha(.7)
  ax.set_title(f'Cache price fraction f={f:g}');ax.axhline(1,color='#888888',linestyle='--',linewidth=.8)
  ax.tick_params(axis='x',labelsize=8);ax.grid(axis='y',alpha=.2);ax.set_ylim(bottom=0)
 axes[0].set_ylabel('Input-only CAF_cache')
 out=ROOT/'figures';out.mkdir(exist_ok=True)
 fig.savefig(out/'fig_task_caf_boxplots.pdf',bbox_inches='tight');fig.savefig(out/'fig_task_caf_boxplots.png',dpi=300,bbox_inches='tight');plt.close(fig)
if __name__=='__main__':plot()
