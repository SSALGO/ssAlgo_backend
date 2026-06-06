import streamlit as st
from pymongo import MongoClient


from pymongo import MongoClient
st.set_page_config(layout="wide")
# MongoDB connection
client = MongoClient('mongodb://localhost:27017')
db = client.demo

orders_collection = db["orders"]
positions_collection = db["positions"]
users_collection = db["users"]
apikeys_collection = db["apis"]
strategy_collection=db['strategies']
history_collection = db["historical"]
opositions_collection=db['Opositions']


user=list(db['users'].find())[0]['username']
#orders=list(orders_collection.find({'user':user}))
positions=list(opositions_collection.find({'user':user}))
apikeys=list(apikeys_collection.find({'user':user}))
strategys=list(strategy_collection.find({'user':user}))
ff=st.data_editor(list(users_collection.find()))
print(ff)
aapi=st.data_editor(apikeys)
print(aapi)
column_configuration={
"gender": st.column_config.SelectboxColumn(
        "Gender", options=["male", "female", "other"]
    )
}
ff1=st.data_editor(strategys)
print(ff1)

#st.data_editor(strategys)

dddf=st.data_editor(positions, num_rows="dynamic")
print(dddf)